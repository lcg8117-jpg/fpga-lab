function [x,motor,actual,valid]=simulate_reference(r,kp,kd)
count=numel(r);x=zeros(count,4);x(1,1)=12;motor=zeros(count,1);actual=zeros(count,1);
oldAngle=single(0);oldPosition=single(0);positionBias=single(0);positionOutput=0;primePosition=true;valid=true;
for n=1:count-1
 bias=single(round(x(n,1)));if n==1,oldAngle=bias;end
 delta=single(double(bias)-double(oldAngle));oldAngle=bias;
 angleP=single(-49*double(bias));angleD=single(251*double(delta));
 angleOutput=fix(double(single(double(angleP)-double(angleD))));
 actual(n+1)=fix(round(x(n,3)*27/26)*26/27);
 if mod(n,5)==0
  least=single(actual(n+1)-r(n+1));
  positionBias=single(double(single(double(positionBias)*.8))+double(least)*.2);
  if primePosition,oldPosition=positionBias;primePosition=false;end
  dp=single(double(positionBias)-double(oldPosition));oldPosition=positionBias;
  positionP=single(double(positionBias)*kp);positionD=single(double(dp)*kd);
  positionOutput=fix(double(single(double(positionP)+double(positionD))));
 end
 motor(n+1)=min(6900,max(-6900,angleOutput-positionOutput));
 p=x(n,:)';u=motor(n);
 for k=1:5
  a=80*p(1)+6*u-.5*p(2);aa=-.02*u-p(4);
  p(2)=p(2)+.001*a;p(1)=p(1)+.001*p(2);
  p(4)=p(4)+.001*aa;p(3)=p(3)+.001*p(4);
 end
 x(n+1,:)=p';
 if any(~isfinite(p)) || abs(p(1))>=800
  valid=false;x(n+1:end,:)=NaN;actual(n+1:end)=NaN;motor(n+1:end)=NaN;break;
 end
end
end

