function screen_control()
root=fileparts(mfilename('fullpath'));cd(root);diary('screen_control.log');
addpath(root);
cases=[25 610;50 610;50 1220;75 610;75 1220;100 610;100 1220;100 2440;150 610;150 1220;150 2440;200 610;200 1220;200 2440;300 1220;300 2440;400 2440;400 4880;600 4880];
scores=struct([]);slot=0;
for lane=1:2
 word='GOWIN';if lane==2,word='EasyStart';end
 data=jsondecode(fileread(fullfile(root,'..','C5_Baseline','data',['Word_' word '.json'])));
 t=readtable(fullfile(root,'..','C5_Baseline','results',sprintf('baseline_lane%d.csv',lane)));
 onset=t.time_s(find(t.state==6,1));lag=round(data.word_metadata.window_ms/5);
 selected=find(t.time_s>=onset+data.word_metadata.word_start_ms/1000-1e-8 & t.time_s<onset+data.word_metadata.word_end_ms/1000-1e-8);
 for c=1:size(cases,1)
  [x,motor,actual,valid]=simulate_reference(t.target,cases(c,1),cases(c,2));
  if c==1
   assert(valid && isequal(motor,t.motor),'Independent nominal RTL motor replay failed');
   assert(max(abs(x-t{:,{'angle_counts','angle_rate','plant_position','plant_velocity'}}),[],'all')<1e-7);
   assert(isequal(actual,t.actual),'Independent nominal encoder replay failed');
  end
  desired=[t.target(selected),(t.target(selected)-t.target(selected-lag))*2*pi/1040/(lag*.005)];
  output=[actual(selected),(actual(selected)-actual(selected-lag))*2*pi/1040/(lag*.005)];
  span=data.word_metadata.span_count;yspan=max(desired(:,2))-min(desired(:,2));
  rmsX=sqrt(mean((output(:,1)-desired(:,1)).^2));rmsY=sqrt(mean((output(:,2)-desired(:,2)).^2));
  cx=corrcoef(output(:,1),desired(:,1));cy=corrcoef(output(:,2),desired(:,2));
  slot=slot+1;
  item=struct('word',word,'p',cases(c,1),'d',cases(c,2),'valid',valid, ...
   'rms_position',rmsX,'actual_span',max(output(:,1))-min(output(:,1)),'rms_velocity_fraction',rmsY/yspan, ...
   'correlation_x',cx(1,2),'correlation_y',cy(1,2),'peak_angle',max(abs(x(:,1))), ...
   'peak_motor',max(abs(motor)), ...
   'numeric_pass',valid && rmsX<=.05*span && max(output(:,1))-min(output(:,1))>=.85*span && max(output(:,1))-min(output(:,1))<=1.15*span && rmsY<=.1*yspan && cx(1,2)>=.95 && cy(1,2)>=.95);
  if slot==1,scores=item;else,scores(slot)=item;end
 end
end
tab=struct2table(scores);writetable(tab,'control_screen.csv');disp(tab);
disp('Exact nominal replay passed. Other rows are analysis only, not actual RTL validation.');
diary off;
end
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
