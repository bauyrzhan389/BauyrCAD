"""SolidWorks gates for M5; only run after the user closes their own session."""
import math
import pytest
from bauyrcad import solidworks
from sw_support import needs_solidworks, reopen
from test_swrevolve import revolved
from test_sw_nativefeatures import _check_part
pytestmark=needs_solidworks


@pytest.mark.parametrize('plane',['front','top','right'])
@pytest.mark.parametrize('angle,mode,reverse',[(360,'blind',False),(145,'blind',False),(75,'blind',True),(100,'midplane',False)])
def test_first_revolve_modes(sw,tmp_path,plane,angle,mode,reverse):
    _check_part(sw,tmp_path,revolved(plane,angle,mode,reverse))


@pytest.mark.parametrize('operation',['add','cut'])
@pytest.mark.parametrize('mode,reverse',[('blind',False),('blind',True),('midplane',False)])
def test_added_revolve_modes(sw,tmp_path,operation,mode,reverse):
    _check_part(sw,tmp_path,revolved(mode=mode,reverse=reverse,operation=operation))


def test_revolve_angle_edit_and_reopen(sw,tmp_path):
    p=revolved(angle=120);path=tmp_path/'angle.SLDPRT'
    p.export_solidworks_offline(path,require_native=True)
    doc=reopen(sw,path)
    dim=sw.T(doc.Parameter('D1@Revolve1'),'IDimension')
    assert dim.SystemValue==pytest.approx(math.radians(120))
    dim.SystemValue=math.radians(80);doc.EditRebuild3()
    p.doc.features[-1].angle=80;p.doc.regenerate()
    assert solidworks.rebuild_health(sw,doc)==(True,[])
    assert solidworks._volume(sw,doc)==pytest.approx(p.volume(),rel=1e-6)
    saved=tmp_path/'angle_saved.SLDPRT';sw.save(doc,saved);sw.close(doc)
    doc=reopen(sw,saved)
    assert solidworks.rebuild_health(sw,doc)==(True,[])
    assert solidworks._volume(sw,doc)==pytest.approx(p.volume(),rel=1e-6)

@pytest.mark.parametrize('rotation,reverse_axis',[(0,False),(37,False),(37,True)])
def test_translated_and_diagonal_revolve_axis(sw,tmp_path,rotation,reverse_axis):
    from bauyrcad.api import Part
    a=math.radians(rotation)
    def xy(x,y):return (55+x*math.cos(a)-y*math.sin(a),-20+x*math.sin(a)+y*math.cos(a))
    p=Part();sk=p.sketch('front')
    sk.polyline([xy(5,3),xy(23,3),xy(23,32),xy(5,32)],close=True)
    ends=[xy(0,-4),xy(0,40)]
    if reverse_axis:ends.reverse()
    axis=sk.centerline(*ends);p.revolve(sk,110,axis=axis)
    _check_part(sw,tmp_path,p)


def test_mixed_revolve_and_extrude_stack(sw,tmp_path):
    p=revolved(angle=360)
    sk=p.sketch('front');sk.circle((20,15),d=5);p.extrude(sk,30)
    sk=p.sketch('front');sk.rect((21,12),(30,22));axis=sk.centerline((0,-4),(0,40))
    p.revolve(sk,90,axis=axis)
    # Independent integral of the circular boss outside the tube, plus the outer
    # quarter-ring, avoids trusting the source kernel's default Gauss approximation.
    from scipy.integrate import quad
    from bauyrcad.core import geometry
    extra,_=quad(lambda x:2*math.sqrt(max(0,6.25-(x-20)**2))*(30-math.sqrt(529-x*x)),
                 17.5,22.5,epsabs=1e-8)
    exact=math.pi*504*29+extra+math.pi/4*(900-529)*10
    assert geometry.accurate_mass_properties(p.doc.body)[0][0]==pytest.approx(exact,rel=1e-9)
    _check_part(sw,tmp_path,p,measure_step=True)

@pytest.mark.parametrize('angle',[180,360])
def test_revolved_circle_with_driving_diameter(sw,tmp_path,angle):
    from bauyrcad.api import Part
    p=Part();sk=p.sketch('front');c=sk.circle((12,5),d=6);sk.dim(c,6)
    axis=sk.centerline((0,-5),(0,15));p.revolve(sk,angle,axis=axis)
    # Analytic torus bounds; OCC's conservative box overestimates its revolved surface.
    _check_part(sw,tmp_path,p,expected_bounds=(-15,2,-15,15,8,0 if angle==180 else 15))


def test_named_sketch_and_revolve_axis_registry(sw,tmp_path):
    p=revolved(operation='add')
    p.doc.features[-2].name='Turn profile';p.doc.features[-1].name='Turn shoulder'
    _check_part(sw,tmp_path,p)
