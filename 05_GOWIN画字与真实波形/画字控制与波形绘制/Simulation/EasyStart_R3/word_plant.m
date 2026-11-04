function word_plant(block)
setup(block);
end
function setup(block)
block.NumDialogPrms=1;
block.NumInputPorts=1;block.NumOutputPorts=7;
block.SetPreCompInpPortInfoToDynamic;block.SetPreCompOutPortInfoToDynamic;
block.InputPort(1).Dimensions=1;block.InputPort(1).DatatypeID=0;
block.InputPort(1).DirectFeedthrough=false;
for k=1:7
 block.OutputPort(k).Dimensions=1;block.OutputPort(k).Complexity='Real';
 block.OutputPort(k).DatatypeID=0;
end
block.OutputPort(1).DatatypeID=4;
block.OutputPort(2).DatatypeID=6;
block.OutputPort(3).DatatypeID=7;
block.SampleTimes=[0.005 0];block.SimStateCompliance='DefaultSimState';
block.RegBlockMethod('PostPropagationSetup',@postprop);
block.RegBlockMethod('InitializeConditions',@initialize);
block.RegBlockMethod('Outputs',@outputs);
block.RegBlockMethod('Update',@update);
end
function postprop(block)
block.NumDworks=1;block.Dwork(1).Name='representative_states';
block.Dwork(1).Dimensions=4;block.Dwork(1).DatatypeID=0;
block.Dwork(1).Complexity='Real';block.Dwork(1).UsedAsDiscState=true;
end
function initialize(block)
block.Dwork(1).Data=[block.DialogPrm(1).Data;0;0;0];
end
function outputs(block)
x=block.Dwork(1).Data;
block.OutputPort(1).Data=int16(round(x(1)));
block.OutputPort(2).Data=int32(round(x(3)*27/26));
block.OutputPort(3).Data=uint32(round(block.CurrentTime/.005)+1);
for k=1:4,block.OutputPort(k+3).Data=x(k);end
end
function update(block)
% These are the supplied Oct27 representative count-domain equations.
% Coefficients are not identified physical IP570/Furuta parameters.
x=block.Dwork(1).Data;u=block.InputPort(1).Data;
assert(isfinite(u) && abs(u)<=6900,'Invalid actual RTL signed motor output');
for k=1:5
 a=80*x(1)+6*u-.5*x(2);
 aa=-.02*u-x(4);
 x(2)=x(2)+.001*a;x(1)=x(1)+.001*x(2);
 x(4)=x(4)+.001*aa;x(3)=x(3)+.001*x(4);
end
assert(all(isfinite(x)) && abs(x(1))<800,'Plant left representative small-angle domain');
block.Dwork(1).Data=x;
end
