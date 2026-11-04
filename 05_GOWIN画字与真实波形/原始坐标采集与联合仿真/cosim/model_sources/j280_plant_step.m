function [next,status] = j280_plant_step(state,p,u,dt)
% Conditional 3-coordinate mechanical model. No controller or sensor inversion.
% p order is mechanical_ensemble.json. Input u=-physical_q15/32768.
mb=p(3)/p(4);mp=mb/p(16);w=2*pi*p(13);
M=[1,-p(3),-mp*p(15);-p(3),mb,0;-mp*p(15),0,mp];
d=mb*p(6);D=[p(2),0,0;0,d,-d;0,-d,d+mp*2*p(14)*w];
K=[0;-mb*p(5);mp*w*w];q=state([1,3,5]);v=state([2,4,6]);q=q(:);v=v(:);
Q=M+dt*D+diag(dt*dt*K);status=0;next=state(:);
if Q(1,1)<=0 || det(Q(1:2,1:2))<=0 || det(Q)<=0,status=-1;return;end
if u>=0,k=p(1);dead=p(10);else,k=p(9);dead=p(11);end
drive=k*sign(u)*max(0,abs(u)-dead)+p(12);
r=M*v-dt*K.*q+[dt*drive;0;0];I=Q\eye(3);v0=I*r;
X=[I(:,1),I(:,2)-I(:,3)];W00=I(1,1);W01=I(1,2)-I(1,3);W11=I(2,2)+I(3,3)-2*I(2,3);
z0=v0(1);z1=v0(2)-v0(3);L0=dt*p(7);L1=dt*mb*p(8);found=false;answer=zeros(3,1);
options=[0,-1,1];
for ii=1:3
 for jj=1:3
  s0=options(ii);s1=options(jj);t0=s0*L0;t1=s1*L1;
  if s0==0 && s1==0
   detW=W00*W11-W01*W01;t0=(W11*z0-W01*z1)/detW;t1=(W00*z1-W01*z0)/detW;
  elseif s0==0,t0=(z0-W01*t1)/W00;
  elseif s1==0,t1=(z1-W01*t0)/W11;end
  x0=z0-W00*t0-W01*t1;x1=z1-W01*t0-W11*t1;
  good0=(s0~=0 && s0*x0>=-1e-12)||(s0==0 && abs(t0)<=L0+1e-12);
  good1=(s1~=0 && s1*x1>=-1e-12)||(s1==0 && abs(t1)<=L1+1e-12);
  if good0 && good1,answer=v0-X*[t0;t1];found=true;break;end
 end
 if found,break;end
end
if ~found,status=-2;return;end
next([1,3,5])=q+dt*answer;next([2,4,6])=answer;
end
