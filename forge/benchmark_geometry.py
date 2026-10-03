"""Reproducible analytic specimens and standard 20-degree involute geometry."""
import math
import numpy as np
import gmsh
from strength.geometry import session


def line_contact(path,length=.0002,radius=.002):
    with session():
        o=gmsh.model.occ
        cylinder=o.addCylinder(0,-length/2,.005+radius,0,length,0,radius)
        cut=o.addBox(-radius*1.1,-length,.005-radius*.1,2.2*radius,2*length,1.1*radius)
        o.intersect([(3,cylinder)],[(3,cut)])
        o.addBox(-.004,-length/2,0,.008,length,.005)
        o.synchronize();gmsh.write(str(path))


def thermal_gap(path,gap=.00001):
    with session():
        o=gmsh.model.occ;o.addBox(-.01,-.01,.01+gap,.02,.02,.01);o.addBox(-.01,-.01,0,.02,.02,.01)
        o.synchronize();gmsh.write(str(path))


def involute_gears(path,teeth=20,module=.001,width=.002,preload_angle=.0003):
    pressure=math.radians(20);rp=module*teeth/2;rb=rp*math.cos(pressure);root=rp-1.25*module;tip=rp+module
    half=math.pi/(2*teeth)+math.tan(pressure)-pressure
    with session():
        o=gmsh.model.occ
        for center,phase in [(-rp,preload_angle),(rp,math.pi/teeth)]:
            origin=o.addPoint(center,0,-width/2);curves=[]
            def point(r,theta):return o.addPoint(center+r*math.cos(theta),r*math.sin(theta),-width/2)
            first=None;last=None
            for tooth in range(teeth):
                phi=phase+tooth*2*math.pi/teeth
                left_root=point(root,phi-half);left_base=point(rb,phi-half)
                if first is None:first=left_root
                if last is not None:curves.append(o.addCircleArc(last,origin,left_root))
                curves.append(o.addLine(left_root,left_base))
                radii=np.linspace(rb,tip,12)
                inv=lambda r:math.sqrt((r/rb)**2-1)-math.acos(rb/r)
                left=[left_base]+[point(float(r),phi-half+inv(r)) for r in radii[1:]]
                right=[point(float(r),phi+half-inv(r)) for r in radii]
                curves.append(o.addBSpline(left));curves.append(o.addCircleArc(left[-1],origin,right[-1]));curves.append(o.addBSpline(right[::-1]))
                right_root=point(root,phi+half);curves.append(o.addLine(right[0],right_root));last=right_root
            curves.append(o.addCircleArc(last,origin,first));wire=o.addCurveLoop(curves)
            bore=o.addCircle(center,0,-width/2,.003);hole=o.addCurveLoop([bore]);face=o.addPlaneSurface([wire,hole]);o.extrude([(2,face)],0,0,width)
        o.synchronize();gmsh.write(str(path))
    return {'teeth':[teeth,teeth],'module_m':module,'pressure_angle_deg':20,'face_width_m':width,'center_distance_m':2*rp,
        'initial_driver_preload_rad':preload_angle,'note':'标准齿制渐开线数值基准；齿根径向连接未加入制造刀具圆角，齿根尖角不用于已验证寿命。'}
