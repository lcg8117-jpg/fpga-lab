function v5_plant(block)
block.NumDialogPrms=0;block.NumInputPorts=5;block.NumOutputPorts=25;
block.SetPreCompInpPortInfoToDynamic;block.SetPreCompOutPortInfoToDynamic;
for k=1:5,block.InputPort(k).Dimensions=1;block.InputPort(k).DatatypeID=0;block.InputPort(k).DirectFeedthrough=false;end
types=[4 6 7 6 4 zeros(1,4) 3 8 8 5 5 6*ones(1,5) 7*ones(1,5) 4];
for k=1:25,block.OutputPort(k).Dimensions=1;block.OutputPort(k).DatatypeID=types(k);block.OutputPort(k).Complexity='Real';end
block.SampleTimes=[.0005 0];block.SimStateCompliance='DefaultSimState';
block.RegBlockMethod('PostPropagationSetup',@postprop);block.RegBlockMethod('InitializeConditions',@initialize);
block.RegBlockMethod('Outputs',@outputs);block.RegBlockMethod('Update',@update);
end
function postprop(block)
block.NumDworks=1;block.Dwork(1).Name='coupled_history_last_edge';block.Dwork(1).Dimensions=3607;
block.Dwork(1).DatatypeID=0;block.Dwork(1).Complexity='Real';block.Dwork(1).UsedAsDiscState=true;
end
function initialize(block)
global BALANCE_PHYSICS BALANCE_STATE_HISTORY
h=repmat(BALANCE_PHYSICS.initial_state(:)',601,1);block.Dwork(1).Data=[h(:);-.006];
BALANCE_STATE_HISTORY=zeros(round(BALANCE_PHYSICS.duration_s*1000)+3,6);BALANCE_STATE_HISTORY(1,:)=h(end,:);
end
function outputs(block)
global BALANCE_PHYSICS
data=block.Dwork(1).Data;h=reshape(data(1:3606),601,6);t=floor((block.CurrentTime+1e-9)/.001)*.001;
[slow,fast,r,rnow,g,adcRaw,adcQ4]=balance_sensors(h,t,BALANCE_PHYSICS);
raw=round(BALANCE_PHYSICS.encoder_sign*h(:,1)*1040/(2*pi));changed=find(diff(raw)~=0,1,'last');last=data(3607);
if ~isempty(changed),last=max(last,t+BALANCE_PHYSICS.history_time(changed+1));end
age=max(0,round((t-last)*50e6));mappingFault=abs(rnow)>65536 || 10000+rnow>32767 || 10000+rnow< -32768;
block.OutputPort(1).Data=int16(slow);block.OutputPort(2).Data=int32(r);
block.OutputPort(3).Data=uint32(floor((block.CurrentTime+1e-9)/.001)+1);
block.OutputPort(4).Data=int32(rnow);block.OutputPort(5).Data=int16(g);
block.OutputPort(6).Data=h(end,3)-h(end,5);block.OutputPort(7).Data=h(end,1);
block.OutputPort(8).Data=h(end,2);block.OutputPort(9).Data=slow;if BALANCE_PHYSICS.fast,block.OutputPort(9).Data=fast;end
block.OutputPort(10).Data=uint8(2*mappingFault);block.OutputPort(11).Data=true;block.OutputPort(12).Data=false;
block.OutputPort(13).Data=uint16(adcRaw);block.OutputPort(14).Data=uint16(adcQ4);
for k=1:5,block.OutputPort(14+k).Data=int32(rnow);block.OutputPort(19+k).Data=uint32(age);end
block.OutputPort(25).Data=int16(fast);
end
function update(block)
global BALANCE_PHYSICS BALANCE_STATE_HISTORY
if mod(round(block.CurrentTime/.0005),2)==0,return;end
data=block.Dwork(1).Data;h=reshape(data(1:3606),601,6);expected=floor((block.CurrentTime+1e-9)/.001)+1;
assert(block.InputPort(4).Data==expected,'RTL transaction incomplete at mechanical update');
[slow,fast]=balance_sensors(h,(expected-1)*.001,BALANCE_PHYSICS);a=slow;if BALANCE_PHYSICS.fast,a=fast;end
assert(block.InputPort(5).Data==a,'RTL returned different fast sensor');
duty=zeros(1,3);for k=1:3,duty(k)=block.InputPort(k).Data/(32768*256);end
assert(all(abs(duty)<.91),'Invalid physical PWM duty');
interval=j280_coupled_interval_mex(h(end,:)',BALANCE_PHYSICS.parameters,duty);
raw=round(BALANCE_PHYSICS.encoder_sign*h(:,1)*1040/(2*pi));changed=find(diff(raw)~=0,1,'last');last=data(3607);
if ~isempty(changed),last=max(last,(expected-1)*.001+BALANCE_PHYSICS.history_time(changed+1));end
h=[h(101:end,:);interval(2:end,:)];assert(all(isfinite(h),'all'),'Nonfinite mechanical state');
block.Dwork(1).Data=[h(:);last];
BALANCE_STATE_HISTORY(expected+1,:)=h(end,:);
end
