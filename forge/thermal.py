"""Scalar isoparametric P2 heat FEM on the exact C3D10 structural nodes."""
import warnings
import numpy as np
from scipy.sparse import coo_matrix,csr_matrix,bmat
from scipy.sparse.linalg import splu,MatrixRankWarning
from strength.fem import tet_shape,tetra_quadrature,surface_weights,IP_BARY
from strength.materials import selection_mask
from .schemas import material_for
from .interfaces import interface_map,conductance_matrix,thermal_conductances

SIGMA=5.670374419e-8


def scalar_matrix(cells,local,size):
    n=cells.shape[1]
    return coo_matrix((local.ravel(),(np.repeat(cells,n,axis=1).ravel(),np.tile(cells,(1,n)).ravel())),shape=(size,size)).tocsr()


class HeatSystem:
    def __init__(self,mesh,case,evidence):
        self.mesh,self.case,self.evidence=mesh,case,evidence
        p=mesh['points'];t=mesh['tets'];self.n=len(p);P=p[t]
        mats=[material_for(case,b) for b in mesh['body_ids']]
        self.k=np.array([m.conductivity_W_mK for m in mats]);capacity=np.array([m.density_kg_m3*m.specific_heat_J_kgK for m in mats])
        localK=np.zeros((len(t),10,10));localM=np.zeros_like(localK);weights=np.zeros((len(t),10));vol=np.zeros(len(t))
        self.quadrature=[];self.phase=[m.phase_change for m in mats];self.density=np.array([m.density_kg_m3 for m in mats]);self.controls={};self.control_values={}
        for q,w in tetra_quadrature(3):
            N,d=tet_shape(q);J=np.einsum('eic,ir->ecr',P,d);det=np.linalg.det(J)
            if np.any(det<=0):raise ValueError('热 P2 积分发现非正 Jacobian')
            grad=np.einsum('ir,erx->eix',d,np.linalg.inv(J));dv=det*w
            self.quadrature.append((N,dv))
            localK+=np.einsum('eix,ejx,e->eij',grad,grad,dv*self.k)
            localM+=np.einsum('i,j,e->eij',N,N,dv*capacity)
            weights+=dv[:,None]*N;vol+=dv
        self.K=scalar_matrix(t,localK,self.n);self.M=scalar_matrix(t,localM,self.n)
        self.volume=vol;self.weights=weights;self.boundaries=[]
        for bc in case.thermal.boundaries:
            if bc.kind=='body_power':
                mask=mesh['body_ids']==bc.body_id
                if not mask.any():raise ValueError('体热源组件不存在')
                f=np.zeros(self.n);np.add.at(f,t[mask].ravel(),(weights[mask]/vol[mask].sum()).ravel())
                self.boundaries.append((bc,None,f,None));continue
            mask=selection_mask(mesh,bc.selection,evidence)
            faces=mesh['faces'][mask];H=np.zeros((len(faces),6,6));f=np.zeros(self.n);quad=[]
            for face,N,normal,norm,w in surface_weights(mesh,mask):
                area=norm*w;H+=np.einsum('i,j,e->eij',N,N,area)
                np.add.at(f,face.ravel(),(area[:,None]*N).ravel());quad.append((face,N,area))
            self.boundaries.append((bc,scalar_matrix(faces,H,self.n),f,quad))
        self.interface_records={}
        for pair in case.contacts:
            if pair.thermal:self.interface_records[pair.name]=interface_map(mesh,pair,evidence)
        self.factor_cache={}
        self.ties=None;self.C=csr_matrix((0,self.n))
        if case.thermal_connections:
            from strength.bonding import prepare_bonds
            from types import SimpleNamespace
            self.ties=prepare_bonds(mesh,SimpleNamespace(bonded_connections=case.thermal_connections),evidence,{},check_anchoring=False)
            rows=[];cols=[];vals=[]
            for i,link in enumerate(self.ties['links']):
                rows.extend([i]*11);cols.extend([link['slave'],*link['master']]);vals.extend([1.,*-link['weights']])
            self.C=coo_matrix((vals,(rows,cols)),shape=(len(self.ties['links']),self.n)).tocsr()

    def interfaces(self,state=None,temperature=None):
        H=csr_matrix((self.n,self.n));audit=[]
        for pair in self.case.contacts:
            if not pair.thermal:continue
            records=self.interface_records[pair.name]
            if state and pair.name in state:
                values=state[pair.name]
                # Pressure and signed gap evaluated at the same persistent quadrature IDs.
                records=[{**r,'gap_m':float(g)} for r,g in zip(records,values['gap_m'])]
                pressure=values['pressure_Pa']
            else:pressure=None
            mean_T=None if temperature is None else [(r['slave_weights']@temperature[r['slave_nodes']]+r['master_weights']@temperature[r['master_nodes']])/2 for r in records]
            h=thermal_conductances(pair,records,pressure,mean_T)
            H+=conductance_matrix(self.n,records,h);audit.append((pair.name,records,h))
        return H,audit

    def operators(self,time,temperature,interface_state=None):
        A=self.K.copy();rhs=np.zeros(self.n);fixed={};radiation=[]
        interface,audit=self.interfaces(interface_state,temperature);A+=interface
        for bi,(bc,H,f,quad) in enumerate(self.boundaries):
            value=float(np.interp(time,[p.x for p in bc.profile],[p.y for p in bc.profile])) if bc.profile else bc.value
            ambient=float(np.interp(time,[p.x for p in bc.ambient_profile],[p.y for p in bc.ambient_profile])) if bc.ambient_profile else bc.ambient_C
            if bc.thermostat:value=self.control_values.get(bi,bc.thermostat.maximum_W)
            active_time=getattr(self,'interval_time',time)
            if bc.kind in ('body_power','surface_power','heat_flux') and (active_time<bc.start_s or (bc.end_s is not None and active_time>=bc.end_s)):value=0.
            if bc.kind=='temperature':
                for node in np.unique(np.concatenate([row[0].ravel() for row in quad])):
                    if node in fixed and abs(fixed[node]-value)>1e-9:raise ValueError('同一节点温度边界冲突')
                    fixed[int(node)]=value
            elif bc.kind=='convection':A+=H*value;rhs+=f*value*ambient
            elif bc.kind in ('surface_power','body_power'):rhs+=f*value/(f.sum() if bc.kind=='surface_power' else 1.)
            elif bc.kind=='heat_flux':rhs+=f*value
            elif bc.kind=='radiation':
                for face,N,area in quad:
                    kelvin=np.maximum(np.einsum('i,ei->e',N,temperature[face])+273.15,.01)
                    deriv=4*value*SIGMA*kelvin**3
                    flux=value*SIGMA*(kelvin**4-(ambient+273.15)**4)
                    A+=scalar_matrix(face,np.einsum('i,j,e->eij',N,N,area*deriv),self.n)
                    np.add.at(rhs,face.ravel(),(area[:,None]*N*(deriv*(kelvin-273.15)-flux)[:,None]).ravel())
                    radiation.append(True)
        return A,rhs,fixed,audit,bool(radiation) or any(v.temperature_C is not None for p in self.case.contacts if p.thermal for v in p.conductance)

    def latent(self,T,previous):
        local=np.zeros((len(self.mesh['tets']),10,10));rhs=np.zeros(self.n)
        if not any(self.phase):return csr_matrix((self.n,self.n)),rhs
        solid=np.array([p.solidus_C if p else 0 for p in self.phase]);width=np.array([p.interval_K if p else 1 for p in self.phase])
        latent=np.array([p.latent_J_kg if p else 0 for p in self.phase])*self.density;t=self.mesh['tets']
        for N,dv in self.quadrature:
            cur=T[t]@N;old=previous[t]@N;fraction=np.clip((cur-solid)/width,0,1)-np.clip((old-solid)/width,0,1)
            derivative=latent/width*((cur>=solid)&(cur<=solid+width))
            local+=np.einsum('i,j,e->eij',N,N,dv*derivative)
            np.add.at(rhs,t.ravel(),(dv[:,None]*latent[:,None]*fraction[:,None]*N).ravel())
        return scalar_matrix(t,local,self.n),rhs

    def control_state(self,previous):
        values={}
        for i,(bc,_,_,_) in enumerate(self.boundaries):
            if bc.thermostat is None:continue
            c=bc.thermostat;nodes=np.unique(self.mesh['faces'][selection_mask(self.mesh,c.sensor,self.evidence)])
            observed=float(np.mean(previous[nodes]));on=self.controls.get(i,True)
            if observed>=c.target_C+c.hysteresis_K/2:on=False
            elif observed<=c.target_C-c.hysteresis_K/2:on=True
            values[i]=c.maximum_W if on else c.minimum_W
        return values

    def solve(self,previous,time,dt=None,interface_state=None,guess=None,commit=True):
        self.interval_time=time if dt is None else time-dt/2
        T=np.array(previous if guess is None else guess,dtype=float)
        previous_multipliers=np.zeros(self.C.shape[0])
        self.control_values=self.control_state(previous)
        phase=dt is not None and any(self.phase)
        for iteration in range(80):
            A,b,fixed,audit,nonlinear=self.operators(time,T,interface_state)
            if dt is None:
                from scipy.sparse.csgraph import connected_components
                graph=A if not self.C.shape[0] else A+abs(self.C).T@abs(self.C)
                count,labels=connected_components(graph,directed=False)
                anchored=set(labels[list(fixed)]) if fixed else set();row_sums=np.asarray(A.sum(1)).ravel()
                totals=np.bincount(labels,weights=row_sums,minlength=count);scales=np.bincount(labels,weights=np.abs(A.diagonal()),minlength=count)
                if any(i not in anchored and totals[i]<=max(scales[i]*1e-12,1e-15) for i in range(count)):
                    raise ValueError('稳态存在未连接到温度／散热边界的绝热连通体；温度无唯一解')
            if dt is not None:A=A+self.M/dt;b=b+self.M@previous/dt
            if phase:
                tangent,enthalpy=self.latent(T,previous);A+=tangent/dt;b+=(tangent@T-enthalpy)/dt
            prescribed=np.array(sorted(fixed),int);free=np.setdiff1d(np.arange(self.n),prescribed)
            new=T.copy()
            if len(prescribed):new[prescribed]=[fixed[n] for n in prescribed];b=b-A[:,prescribed]@new[prescribed]
            if len(free):
                matrix=A[free][:,free].tocsc()
                right=b[free];Cf=self.C[:,free]
                if self.C.shape[0]:
                    matrix=bmat([[matrix,Cf.T],[Cf,None]],format='csc')
                    right=np.r_[right,-self.C[:,prescribed]@new[prescribed]]
                try:
                    import hashlib
                    key=hashlib.sha256(matrix.data.tobytes()+matrix.indices.tobytes()+matrix.indptr.tobytes()+free.tobytes()).hexdigest()
                    if key not in self.factor_cache:
                        if len(self.factor_cache)>3:self.factor_cache.clear()
                        self.factor_cache[key]=splu(matrix)
                    solved=self.factor_cache[key].solve(right);new[free]=solved[:len(free)];multipliers=solved[len(free):]
                except RuntimeError as exc:raise ValueError('热方程奇异：稳态各连通体需要温度或有效散热边界') from exc
            if not np.isfinite(new).all() or new.min() < -273.15:raise ValueError('热求解产生无效温度')
            if phase:
                def residual_norm(candidate,multiplier):
                    AA,bb,_,_,_=self.operators(time,candidate,interface_state)
                    rr=AA@candidate-bb+self.M@(candidate-previous)/dt+self.latent(candidate,previous)[1]/dt
                    if self.C.shape[0]:rr+=self.C.T@multiplier
                    return np.linalg.norm(rr[free])
                base=residual_norm(T,previous_multipliers);alpha=1.
                direction=new-T;target_multiplier=multipliers if self.C.shape[0] else previous_multipliers
                for _ in range(24):
                    candidate=T+alpha*direction;candidate[prescribed]=new[prescribed]
                    lm=previous_multipliers+alpha*(target_multiplier-previous_multipliers)
                    if residual_norm(candidate,lm)<=max(base*(1-1e-4*alpha),1e-9):break
                    alpha/=2
                else:
                    from .errors import NonlinearConvergenceError
                    raise NonlinearConvergenceError('相变焓迭代线搜索失败；请缩短时间步')
                new=candidate;multipliers=lm;previous_multipliers=lm
            error=float(np.max(np.abs(new-T)))
            T=new
            if not (nonlinear or phase) or error<1e-7:break
        else:
            from .errors import NonlinearConvergenceError
            raise NonlinearConvergenceError('辐射／焓迭代未收敛，请缩短时间步')
        actualA,actualB,fixed,audit,_=self.operators(time,T,interface_state)
        residual=actualA@T-actualB
        storage=np.zeros(self.n) if dt is None else self.M@(T-previous)/dt
        if phase:storage+=self.latent(T,previous)[1]/dt
        residual+=storage
        if self.C.shape[0]:residual+=self.C.T@multipliers
        scale=max(float(np.abs(actualB).sum()),float(np.abs(actualA@T).sum()),1.)
        relative=float(np.linalg.norm(residual[free],np.inf)/scale) if len(free) else 0.
        if relative>1e-7:
            from .errors import NonlinearConvergenceError
            raise NonlinearConvergenceError('热方程残差超限')
        for body in np.unique(self.mesh['body_ids']):
            m=material_for(self.case,body)
            if m.valid_temperature_C:
                nodes=np.unique(self.mesh['tets'][self.mesh['body_ids']==body]);lo,hi=m.valid_temperature_C
                if T[nodes].min()<lo or T[nodes].max()>hi:raise ValueError(f'组件 {body} 温度超出材料参数适用范围')
        if commit:
            for i,value in self.control_values.items():self.controls[i]=value==self.boundaries[i][0].thermostat.maximum_W
        interfaces=[]
        for name,records,h in audit:
            flows=[v*r['area_m2']*(r['slave_weights']@T[r['slave_nodes']]-r['master_weights']@T[r['master_nodes']]) for r,v in zip(records,h)]
            interfaces.append({'name':name,'heatflow_W':float(sum(flows)),'point_heatflow_W':flows,'conductance_W_m2K':h.tolist()})
        if self.ties:
            for pi,pair in enumerate(self.case.thermal_connections):
                flows=[float(multipliers[i]) for i,link in enumerate(self.ties['links']) if link['pair']==pi]
                interfaces.append({'name':pair.name,'type':'ideal_thermal_tie','heatflow_W':sum(flows),'point_heatflow_W':flows})
        net=float((actualB-actualA@T).sum());reaction=float(residual[prescribed].sum());stored=float(storage.sum())
        energy_error=abs(net+reaction-stored)/max(abs(net),abs(reaction),abs(stored),1.)
        return T,{'residual_relative':relative,'storage_W':stored,'net_boundary_input_W':net,'prescribed_temperature_reaction_W':reaction,
            'energy_balance_relative':energy_error,'interfaces':interfaces,'iterations':iteration+1}

    def gradients(self,T):
        P=self.mesh['points'][self.mesh['tets']];values=T[self.mesh['tets']];gradients=[]
        for bary in IP_BARY:
            N,d=tet_shape(bary[1:]);J=np.einsum('eic,ir->ecr',P,d)
            grad=np.einsum('ir,erx->eix',d,np.linalg.inv(J))
            gradients.append(np.einsum('ei,eix->ex',values,grad))
        gradient=np.stack(gradients,axis=1)
        return gradient,-self.k[:,None,None]*gradient


def solve_history(system,case,progress=lambda *args:None,initial=None,checkpoint=None,journal_folder=None):
    settings=case.thermal
    if initial is None:
        from .initial_state import load_initial_temperature
        initial,_=load_initial_temperature(system.mesh,case)
    T=np.asarray(initial,dtype=float).copy()
    if T.shape!=(system.n,) or not np.isfinite(T).all() or T.min() < -273.15:raise ValueError('初始温度场无效')
    times=[];frames=[];audits=[];saved_indices=[]
    if journal_folder is not None:
        from .checkpoints import append_step,JournalSeries
        checkpoint=lambda i,t,T,a:append_step(journal_folder,i,t,T,audit=a)
        frames=JournalSeries(journal_folder,saved_indices,'temperature_C')
    if case.analysis_type=='thermal_steady':
        T,a=system.solve(T,0)
        if checkpoint:checkpoint(0,0.,T,a)
        if journal_folder is not None:saved_indices.append(0)
        return [0.],frames if journal_folder is not None else [T],[a]
    times.append(0.);audits.append({'initial':True})
    if journal_folder is not None:saved_indices.append(0)
    else:frames.append(T.copy())
    if checkpoint:checkpoint(0,0.,T,audits[0])
    time=0.;next_save=min(settings.save_s,settings.duration_s)
    while time<settings.duration_s-1e-10:
        dt=next_step(settings,time,next_save)
        T,a=system.solve(T,time+dt,dt);time+=dt
        a.update(time_s=time,dt_s=dt);audits.append(a)
        if checkpoint:checkpoint(len(audits)-1,time,T,a)
        progress('solving',f'热瞬态 {time:g} / {settings.duration_s:g} s')
        if time>=next_save-1e-9:
            times.append(time)
            if journal_folder is not None:saved_indices.append(len(audits)-1)
            else:frames.append(T.copy())
            a['saved_frame']=len(times)-1;next_save=min(next_save+settings.save_s,settings.duration_s)
    return times,frames,audits


def next_step(settings,time,next_save):
    """Land on source switches and piecewise history knots before advancing."""
    events=[settings.duration_s,next_save]
    for bc in settings.boundaries:
        events += [bc.start_s,*[p.x for p in bc.profile],*[p.x for p in bc.ambient_profile]]
        if bc.end_s is not None:events.append(bc.end_s)
    return min(settings.dt_s,min(t-time for t in events if t>time+1e-10))
