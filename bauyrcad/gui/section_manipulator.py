"""Display-space handles for a world-space section plane (no model topology)."""
import math
import numpy as np
import pyvista as pv
import vtk

class SectionManipulator:
    def __init__(self, control):
        self.control=control
        self.viewer=control.main.viewer
        self.actors=[]
        self.paths={}
        self.drag=None
        self.capture_hidden=False
        for color in [(0.2,0.4,0.8),(.8,.2,.15),(.2,.6,.25)]:
            mapper=vtk.vtkPolyDataMapper2D()
            actor=vtk.vtkActor2D(); actor.SetMapper(mapper)
            actor.GetProperty().SetColor(*color); actor.GetProperty().SetLineWidth(3)
            actor.PickableOff(); self.viewer.renderer.AddViewProp(actor)
            self.actors.append(actor)

    def enabled(self):
        c=self.control
        return not self.capture_hidden and c.isVisible() and c.handles.isChecked() and not c.awaiting_face and self.viewer.section.active

    def update(self):
        enabled=self.enabled()
        for actor in self.actors: actor.SetVisibility(enabled)
        if not enabled:
            self.paths={}; self.drag=None
            return
        c=self.control; origin,n=c.basis(); origin=origin+n*c.offset.value()
        center=c.model_center
        pivot=center-n*np.dot(center-origin,n)
        cam=self.viewer.renderer.GetActiveCamera()
        height=max(self.viewer.renderer.GetSize()[1],1)
        if cam.GetParallelProjection(): span=2*cam.GetParallelScale()
        else:
            depth=abs(np.dot(pivot-np.array(cam.GetPosition()),cam.GetDirectionOfProjection()))
            span=2*depth*math.tan(math.radians(cam.GetViewAngle()/2))
        self.units_per_pixel=max(span/height,1e-8)
        radius=65*self.viewer.plotter.interactor.devicePixelRatioF()*self.units_per_pixel
        def project(points): return self.viewer.project(np.asarray(points))[0]
        self.pivot_xy=project([pivot])[0]
        arrow=project([pivot,pivot+n*radius*1.5])
        direction=arrow[1]-arrow[0]
        if np.linalg.norm(direction)<12:
            direction=np.array([0.,85.])*self.viewer.plotter.interactor.devicePixelRatioF()
            arrow=np.array([self.pivot_xy,self.pivot_xy+direction])
        self.translation_vector=direction/(radius*1.5)
        unit=direction/np.linalg.norm(direction); side=np.array([-unit[1],unit[0]])
        tip=arrow[1]
        self.paths={'offset':np.array([arrow[0],tip,tip-unit*12+side*6,tip,tip-unit*12-side*6])}
        frame=c.rotation_frame()
        theta=np.linspace(0,2*np.pi,97)
        self.ring_bases={}
        for name,axis in [('x',frame[:,0]),('y',frame[:,1])]:
            u=n; v=np.cross(axis,u); v/=np.linalg.norm(v)
            points=pivot+radius*(np.cos(theta)[:,None]*u+np.sin(theta)[:,None]*v)
            self.paths[name]=project(points)
            self.ring_bases[name]=np.column_stack([project([pivot+radius*u])[0]-self.pivot_xy,
                                                 project([pivot+radius*v])[0]-self.pivot_xy])
        for actor,path in zip(self.actors,self.paths.values()):
            points=np.column_stack([path,np.zeros(len(path))])
            mesh=pv.PolyData(points); mesh.lines=np.r_[len(path),np.arange(len(path))]
            actor.GetMapper().SetInputData(mesh)

    def press(self,x,y):
        if not self.enabled(): return False
        point=np.array([x,y]); best=(9*self.viewer.plotter.interactor.devicePixelRatioF(),None)
        for name,path in self.paths.items():
            a,b=path[:-1],path[1:]; d=b-a
            t=np.clip(np.sum((point-a)*d,axis=1)/np.maximum(np.sum(d*d,axis=1),1e-10),0,1)
            distance=np.linalg.norm(a+t[:,None]*d-point,axis=1).min()
            if distance<best[0]: best=(distance,name)
        if best[1] is None: return False
        name=best[1]
        self.drag=dict(name=name,start=point,value=self.control.offset.value() if name=='offset' else
                       self.control.angles[0 if name=='x' else 1].value(),
                       vector=self.translation_vector.copy(),pivot=self.pivot_xy.copy(),
                       basis=self.ring_bases.get(name),last=0.,total=0.)
        return True

    def move(self,x,y):
        if self.drag is None: return
        d=self.drag; delta=np.array([x,y])-d['start']; name=d['name']
        if name=='offset':
            vector=d['vector']; value=d['value']+np.dot(delta,vector)/max(np.dot(vector,vector),1e-12)
            self.control.offset.setValue(value)
        else:
            basis=d['basis']
            if abs(np.linalg.det(basis))>25:
                a=np.linalg.solve(basis,d['start']-d['pivot'])
                b=np.linalg.solve(basis,np.array([x,y])-d['pivot'])
                angle=math.degrees(math.atan2(b[1],b[0])-math.atan2(a[1],a[0]))
                change=(angle-d['last']+180)%360-180
                d['total']+=change; d['last']=angle
                value=d['value']+d['total']
            else: value=d['value']+(delta[0]+delta[1])*.5
            self.control.angles[0 if name=='x' else 1].setValue(value)

    def release(self): self.drag=None
