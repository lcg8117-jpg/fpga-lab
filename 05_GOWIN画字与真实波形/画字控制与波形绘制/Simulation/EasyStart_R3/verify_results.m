function verify_results()
root=fileparts(mfilename('fullpath'));cd(root);
diary(fullfile(root,'logs','native_matlab_crosscheck.log'));
native=readtable(fullfile(root,'results','native_sampled.csv'));
assert(height(native)==48000,'Incomplete native WLF extraction');
names={'motor','target','actual','tracking','completed','accepted','draw_index'};
metrics=struct([]);
for lane=1:2
 if lane==1,word='GOWIN';else,word='EasyStart';end
 t=readtable(fullfile(root,'results',sprintf('font2_lane%d.csv',lane)));
 w=native(native.lane==lane,:);
 assert(height(w)==24000 && max(abs(w.time_s-t.time_s(2:end)))<1e-10);
 assert(isequal(w{:,names},t{2:end,names}),'Native WLF and Simulink output disagree');
 assert(all(abs(w.correction)<=256),'Input correction bound');
 % Independent original PID replay, using the recorded virtual error.
 oldAngle=single(0);oldPosition=single(0);positionBias=single(0);positionOutput=0;primePosition=true;
 independent=zeros(height(w),1);
 for n=1:height(w)
  bias=single(round(t.angle_counts(n)));if n==1,oldAngle=bias;end
  delta=single(double(bias)-double(oldAngle));oldAngle=bias;
  angleP=single(-49*double(bias));angleD=single(251*double(delta));
  angleOutput=fix(double(single(double(angleP)-double(angleD))));
  if mod(n,5)==0
   least=single(w.actual(n)-w.target(n)-w.correction(n));
   positionBias=single(double(single(double(positionBias)*.8))+double(least)*.2);
   if primePosition,oldPosition=positionBias;primePosition=false;end
   dp=single(double(positionBias)-double(oldPosition));oldPosition=positionBias;
   positionP=single(double(positionBias)*25);positionD=single(double(dp)*610);
   positionOutput=fix(double(single(double(positionP)+double(positionD))));
  end
  independent(n)=min(6900,max(-6900,angleOutput-positionOutput));
 end
 assert(isequal(independent,w.motor),'Actual RTL motor differs from independent original PID replay');
 input=jsondecode(fileread(fullfile(root,'data',['Word_' word '.json'])));meta=input.word_metadata;
 first=find(t.state==6,1);lag=round(meta.window_ms/5);onset=t.time_s(first);
 ix=find(t.time_s-onset>=meta.word_start_ms/1000-1e-8 & t.time_s-onset<meta.word_end_ms/1000-1e-8);
 measured=[t.actual(ix),(t.actual(ix)-t.actual(ix-lag))*2*pi/1040/(meta.window_ms/1000)];
 reference=[t.target(ix),(t.target(ix)-t.target(ix-lag))*2*pi/1040/(meta.window_ms/1000)];
 error=measured-reference;rmsValue=sqrt(mean(error.^2));c=corrcoef(measured(:,1),reference(:,1));cx=c(1,2);
 c=corrcoef(measured(:,2),reference(:,2));cy=c(1,2);
 intendedSpan=[meta.span_count,max(reference(:,2))-min(reference(:,2))];
 span=max(measured)-min(measured);spanRatio=span(1)/meta.span_count;velocityFraction=rmsValue(2)/intendedSpan(2);
 numericPass=rmsValue(1)<=.05*meta.span_count && spanRatio>=.85 && spanRatio<=1.15 && velocityFraction<=.1 && min(cx,cy)>=.95;
 assert(numericPass,'Predeclared same-time coordinate criteria failed');
 assert(t.done(end)==1 && t.settled(end)==1 && t.state(end)==0 && t.target(end)==0 && t.actual(end)==0);
 if lane==1
  previous=readtable(fullfile(root,'..','Final','results','final_lane1.csv'));
  assert(isequal(t{:,:},previous{:,:}),'The approved GOWIN changed');
 end
 m=struct('word',word,'same_time_position_rms_counts',rmsValue(1),'position_rms_limit_counts',.05*meta.span_count, ...
  'actual_span_counts',span(1),'intended_span_counts',meta.span_count,'span_fraction_of_intended',spanRatio,'mean_velocity_rms_fraction',velocityFraction, ...
  'x_correlation',cx,'y_correlation',cy,'numeric_pass',numericPass,'lag_fit_samples',0, ...
  'native_csv_mismatches',0,'independent_pid_mismatches',0,'maximum_input_correction_counts',max(abs(w.correction)), ...
  'return_and_settle_pass',true,'output_smoothing',false,'physical_validation',false, ...
  'maximum_angle_counts',max(abs(t.angle_counts)),'maximum_motor_request',max(abs(w.motor)), ...
  'motor_limit_samples',sum(abs(w.motor)==6900));
 if lane==1,metrics=m;else,metrics(lane)=m;end
 fprintf('%s_NATIVE_AND_INDEPENDENT_PID_PASS rows=%d rms=%.9f Xcorr=%.9f Ycorr=%.9f\n',word,height(w),rmsValue(1),cx,cy);
end
load_system(fullfile(root,'C6_Word_ClosedLoop_font2.slx'));
load_system(fullfile(root,'C6_Word_ClosedLoop_review.slx'));
a=functionalGraph('C6_Word_ClosedLoop_font2');b=functionalGraph('C6_Word_ClosedLoop_review');
assert(isequal(a,b),'Presentation model changed computational settings or connections');
close_system('C6_Word_ClosedLoop_font2',0);close_system('C6_Word_ClosedLoop_review',0);
result=struct('status','PASS','native_rows',48000,'native_fields_per_row',8,'results',metrics, ...
 'review_model_functionally_identical',true,'block_count',numel(a.blocks),'connection_count',numel(a.connections), ...
 'visual_review','Tracks inherited rounded target contours with visible integer stairs; not typeset lettering', ...
 'model_specific_compensation',true,'physical_validation',false);
fid=fopen(fullfile(root,'results','native_crosscheck.json'),'w');fprintf(fid,'%s',jsonencode(result,PrettyPrint=true));fclose(fid);
disp(result);disp('NATIVE_DATA_PID_AND_PREDECLARED_CRITERIA_PASS');diary off;
end

function result=functionalGraph(mdl)
keys={'BlockType','FunctionName','Parameters','OutDataTypeStr','Inputs','VariableName','SaveFormat', ...
 'PortPaths','PortModes','PortTimes','PortSigns','PortFracLengths','PortWordLengths', ...
 'PortHDLWordSizes','ClockPaths','ClockModes','ClockTimes','CommLocal','CommSharedMemory', ...
 'CommPortNumber','TimingMode','TimingScaleFactor','RunAutoTimescale','AllowDirectFeedthrough', ...
 'TclPreSimCommand','TclPostSimCommand'};
paths=sort(find_system(mdl,'SearchDepth',1,'Type','Block'));blocks=cell(numel(paths),1);
for n=1:numel(paths)
 x=struct('path',erase(paths{n},[mdl '/']));
 for k=1:numel(keys),try,x.(keys{k})=get_param(paths{n},keys{k});catch,end,end
 blocks{n}=x;
end
connections={};lines=find_system(mdl,'FindAll','on','SearchDepth',1,'Type','line');
for h=lines(:)'
 source=get_param(h,'SrcBlockHandle');srcPort=get_param(h,'SrcPortHandle');
 destinations=get_param(h,'DstBlockHandle');dstPorts=get_param(h,'DstPortHandle');
 if source<0 || srcPort<0,continue;end
 for k=1:numel(destinations)
  if destinations(k)<0 || dstPorts(k)<0,continue;end
  connections{end+1,1}=sprintf('%s/%d -> %s/%d',erase(getfullname(source),[mdl '/']),get_param(srcPort,'PortNumber'), ...
   erase(getfullname(destinations(k)),[mdl '/']),get_param(dstPorts(k),'PortNumber'));
 end
end
result=struct('blocks',{blocks},'connections',{unique(connections)});
end
