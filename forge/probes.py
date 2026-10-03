"""Displacement-based signed clearance changes on paired independent surfaces."""
from types import SimpleNamespace
import numpy as np
from .interfaces import interface_map


def clearance_history(mesh,case,evidence,structures,times):
    output={}
    for probe in case.clearance_probes:
        pair=SimpleNamespace(name=probe.name,slave=probe.inner,master=probe.outer,initial_offset_m=0.,search_distance_m=probe.search_distance_m)
        records=interface_map(mesh,pair,evidence)
        initial=np.array([r['gap_m'] if probe.nominal_gap_m is None else probe.nominal_gap_m for r in records])
        gaps=[]
        for frame in structures:
            u=frame['displacements']
            delta=[(r['slave_weights']@u[r['slave_nodes']]-r['master_weights']@u[r['master_nodes']])@r['normal'] for r in records]
            gaps.append(initial+delta)
        weights=np.array([r['area_m2'] for r in records]);values=np.asarray(gaps)
        output[probe.name]={'times_s':times,'minimum_gap_m':values.min(1).tolist(),'maximum_gap_m':values.max(1).tolist(),
            'mean_gap_m':(values@weights/weights.sum()).tolist(),'initial_geometry_gap_m':float(np.mean(initial)),
            'source':probe.source,'method':'T6 interpolation of displacement difference along initial outer-surface normal; small-displacement clearance probe',
            'points_m':np.array([r['position_m'] for r in records]),'gap_m':values}
    return output
