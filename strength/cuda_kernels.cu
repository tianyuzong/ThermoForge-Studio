// FP64 isoparametric C3D10, engineering shear in B; tensor shear in output.
extern "C" __global__ void gradients(const double* x, const long long* t,
 const double* deriv, double* g, double* weights, int* invalid, int ne) {
 int z=blockDim.x*blockIdx.x+threadIdx.x; if(z>=ne*4)return;
 int e=z/4,q=z%4; double j[9]={0};
 for(int n=0;n<10;n++)for(int a=0;a<3;a++)for(int b=0;b<3;b++)
   j[a*3+b]+=x[t[e*10+n]*3+a]*deriv[q*30+n*3+b];
 double det=j[0]*(j[4]*j[8]-j[5]*j[7])-j[1]*(j[3]*j[8]-j[5]*j[6])+j[2]*(j[3]*j[7]-j[4]*j[6]);
 if(!(det>0)||!isfinite(det)){atomicExch(invalid,1);return;}
 double inv[9]={(j[4]*j[8]-j[5]*j[7])/det,(j[2]*j[7]-j[1]*j[8])/det,(j[1]*j[5]-j[2]*j[4])/det,
 (j[5]*j[6]-j[3]*j[8])/det,(j[0]*j[8]-j[2]*j[6])/det,(j[2]*j[3]-j[0]*j[5])/det,
 (j[3]*j[7]-j[4]*j[6])/det,(j[1]*j[6]-j[0]*j[7])/det,(j[0]*j[4]-j[1]*j[3])/det};
 weights[z]=det/24.;
 for(int n=0;n<10;n++)for(int a=0;a<3;a++){
   double v=0;for(int b=0;b<3;b++)v+=deriv[q*30+n*3+b]*inv[b*3+a];
   g[z*30+n*3+a]=v;
 }
}
extern "C" __global__ void assemble(const long long* t,const double* g,const double* w,
 const int* map,const int* ptr,const int* col,double* values,const double* lambdas,const double* mus,int start,int ne){
 int z=blockDim.x*blockIdx.x+threadIdx.x;if(z>=ne*900)return;
 int e=z/900,i=(z%900)/30,j=z%30,n=i/3,m=j/3,a=i%3,b=j%3;
 double lambda=lambdas[start+e],mu=mus[start+e];
 int row=map[t[e*10+n]*3+a],target=map[t[e*10+m]*3+b];if(row<0||target<0)return;
 int lo=ptr[row],hi=ptr[row+1];while(lo<hi){int mid=lo+(hi-lo)/2;if(col[mid]<target)lo=mid+1;else hi=mid;}
 double v=0;
 for(int q=0;q<4;q++){
   const double* u=g+(e*4+q)*30+n*3; const double* vj=g+(e*4+q)*30+m*3;
   double dot=u[0]*vj[0]+u[1]*vj[1]+u[2]*vj[2];
   v+=(lambda*u[a]*vj[b]+mu*u[b]*vj[a]+(a==b?mu*dot:0))*w[e*4+q];
 }
 atomicAdd(values+lo,v);
}
extern "C" __global__ void recover(const long long* t,const double* g,const double* u,
 const double* weights,double* stress,double* strain,double* internal,const double* lambdas,const double* mus,int start,int ne){
 int z=blockDim.x*blockIdx.x+threadIdx.x;if(z>=ne*4)return;
 int e=z/4;double h[9]={0};
 double lambda=lambdas[start+e],mu=mus[start+e];
 for(int n=0;n<10;n++)for(int a=0;a<3;a++)for(int b=0;b<3;b++)h[a*3+b]+=u[t[e*10+n]*3+a]*g[z*30+n*3+b];
 double tr=h[0]+h[4]+h[8];double ep[6]={h[0],h[4],h[8],(h[1]+h[3])/2,(h[2]+h[6])/2,(h[5]+h[7])/2};
 for(int a=0;a<6;a++){strain[z*6+a]=ep[a];stress[z*6+a]=2*mu*ep[a]+(a<3?lambda*tr:0);}
 double s[9]={stress[z*6],stress[z*6+3],stress[z*6+4],stress[z*6+3],stress[z*6+1],stress[z*6+5],stress[z*6+4],stress[z*6+5],stress[z*6+2]};
 for(int n=0;n<10;n++)for(int a=0;a<3;a++){
 double v=0;for(int b=0;b<3;b++)v+=s[a*3+b]*g[z*30+n*3+b];
 atomicAdd(internal+t[e*10+n]*3+a,v*weights[z]);}
}
extern "C" __global__ void gather_rhs(const int* free,const double* force,const double* internal,double* b,int n){int i=blockIdx.x*blockDim.x+threadIdx.x;if(i<n)b[i]=force[free[i]]-internal[free[i]];}
extern "C" __global__ void scatter(const int* free,const double* x,double* u,int n){int i=blockIdx.x*blockDim.x+threadIdx.x;if(i<n)u[free[i]]=x[i];}
