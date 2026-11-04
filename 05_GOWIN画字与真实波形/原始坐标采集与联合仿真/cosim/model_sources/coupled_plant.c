#include <math.h>
#include <stdint.h>
#ifdef _WIN32
#define API __declspec(dllexport)
#else
#define API
#endif
/* Offline three-coordinate plant, never a board controller.
 * q=[theta,beta,psi], measured joint angle beta-psi.
 * Positive mass, support spring and dissipation. Backward Euler integration
 * with two Coulomb contacts solved as a bounded impulse complementarity.
 */
API int support_step(double *s,const double *p,double u,double dt){
 double mb=p[2]/p[3],mp=mb/p[15],w=2.*3.141592653589793*p[12];
 double M[9]={1,-p[2],-mp*p[14],-p[2],mb,0,-mp*p[14],0,mp};
 double damp=mb*p[5];
 double D[9]={p[1],0,0,0,damp,-damp,0,-damp,damp+mp*2*p[13]*w};
 double K[3]={0,-mb*p[4],mp*w*w};
 double Q[9],I[9],r[3]={0},v0[3]={0},q[3]={s[0],s[2],s[4]},v[3]={s[1],s[3],s[5]};
 for(int i=0;i<9;i++)Q[i]=M[i]+dt*D[i];
 for(int i=0;i<3;i++){Q[4*i]+=dt*dt*K[i];r[i]=-dt*K[i]*q[i];for(int j=0;j<3;j++)r[i]+=M[3*i+j]*v[j];}
 double drive=(u>=0?p[0]:p[8])*copysign(fmax(0,fabs(u)-(u>=0?p[9]:p[10])),u)+p[11];
 r[0]+=dt*drive;
 double a=Q[0],b=Q[1],c=Q[2],d=Q[4],e=Q[5],f=Q[8];
 double det=a*(d*f-e*e)-b*(b*f-e*c)+c*(b*e-d*c);
 if(!(a>0&&a*d-b*b>0&&det>0))return -1;
 I[0]=(d*f-e*e)/det;I[1]=I[3]=(c*e-b*f)/det;I[2]=I[6]=(b*e-c*d)/det;
 I[4]=(a*f-c*c)/det;I[5]=I[7]=(b*c-a*e)/det;I[8]=(a*d-b*b)/det;
 for(int i=0;i<3;i++)for(int j=0;j<3;j++)v0[i]+=I[3*i+j]*r[j];
 double X[6]={I[0],I[1]-I[2],I[3],I[4]-I[5],I[6],I[7]-I[8]};
 double W00=I[0],W01=I[1]-I[2],W11=I[4]+I[8]-2*I[5];
 double z0=v0[0],z1=v0[1]-v0[2],limit0=dt*p[6],limit1=dt*mb*p[7];
 int found=0;double answer[3]={0};
 const int options[3]={0,-1,1};
 for(int ii=0;ii<3&&!found;ii++)for(int jj=0;jj<3&&!found;jj++){
   int s0=options[ii],s1=options[jj];double t0=s0*limit0,t1=s1*limit1;
   if(!s0&&!s1){double dd=W00*W11-W01*W01;t0=(W11*z0-W01*z1)/dd;t1=(W00*z1-W01*z0)/dd;}
   else if(!s0)t0=(z0-W01*t1)/W00;
   else if(!s1)t1=(z1-W01*t0)/W11;
   double x0=z0-W00*t0-W01*t1,x1=z1-W01*t0-W11*t1;
   int good0=s0?s0*x0>=-1e-12:fabs(t0)<=limit0+1e-12;
   int good1=s1?s1*x1>=-1e-12:fabs(t1)<=limit1+1e-12;
   if(good0&&good1){for(int i=0;i<3;i++)answer[i]=v0[i]-X[2*i]*t0-X[2*i+1]*t1;found=1;}
 }
 if(!found)return -2;
 for(int i=0;i<3;i++){s[2*i]+=dt*answer[i];s[2*i+1]=answer[i];}
 return 0;
}
