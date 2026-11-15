from __future__ import annotations
import numpy as np
from .coordinates import project

class Coverage:
    def __init__(self,geometry,config):
        self.config=config
        self.points,self.normals,self.areas,self.faces=geometry.samples(config)
        self.target=np.ones(len(self.points),dtype=bool)
        self.covered=np.zeros(len(self.points),dtype=bool)
        self.unreachable=np.zeros(len(self.points),dtype=bool)
        self.started=False;self.definition='all eligible surfaces'
    def define(self,center=None,radius=None):
        if self.started: raise ValueError('Target definition is frozen. Reset the task before changing it.')
        target=np.ones(len(self.points),dtype=bool) if center is None else np.linalg.norm(self.points-center,axis=1)<=radius
        if not target.any(): raise ValueError('Selected region contains no sampled eligible surface. Increase radius or select another region.')
        self.target=target;self.covered[:]=False;self.unreachable[:]=False
        self.definition='all eligible surfaces' if center is None else 'selected sphere radius %.3f m'%radius
    def geometric_candidates(self,position,rotation,k):
        uv,depth=project(self.points,position,rotation,k)
        delta=np.asarray(position)-self.points;distance=np.linalg.norm(delta,axis=1)
        cosines=np.sum(self.normals*delta/np.maximum(distance[:,None],1e-9),axis=1)
        lo,hi=self.config.distance_range
        # Projected pixels per metre includes surface foreshortening.
        ppm=k['fx']*np.maximum(cosines,0)/np.maximum(depth,1e-9)
        return self.target & (depth>0) & (uv[:,0]>=0)&(uv[:,0]<k['width'])&(uv[:,1]>=0)&(uv[:,1]<k['height']) & (distance>=lo)&(distance<=hi)&(cosines>=np.cos(np.deg2rad(self.config.max_view_angle)))&(ppm>=self.config.minimum_pixels_per_meter)
    def visible_mask(self,position,rotation,k,visibility):
        mask=self.geometric_candidates(position,rotation,k)
        indices=np.flatnonzero(mask)
        mask[indices]=visibility(position,self.points[indices])
        return mask
    def observe(self,observation,visibility):
        if not self.started: return
        mask=self.visible_mask(observation.position,observation.rotation,observation.intrinsics,visibility)
        self.covered|=mask;self.unreachable[mask]=False
    def summary(self):
        target_area=float(self.areas[self.target].sum())
        inspected=float(self.areas[self.target&self.covered].sum())
        unreachable=float(self.areas[self.target&~self.covered&self.unreachable].sum())
        return dict(fraction=inspected/target_area if target_area else 0,total_area_m2=target_area,
                    inspected_area_m2=inspected,pending_area_m2=max(0,target_area-inspected-unreachable),unreachable_area_m2=unreachable,
                    definition=self.definition,samples=int(self.target.sum()),estimator='area-weighted Monte Carlo surface samples')
    def colors(self):
        colors=np.zeros(len(self.points),dtype=int)
        colors[self.target]=1;colors[self.target&self.unreachable]=3;colors[self.target&self.covered]=2
        return colors.tolist()
