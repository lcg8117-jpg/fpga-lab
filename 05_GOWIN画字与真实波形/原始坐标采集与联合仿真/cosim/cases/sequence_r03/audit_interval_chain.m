function result=audit_interval_chain(root)
% Verify saved causal state transitions, not a new controller prediction.
names={'signfix_sensor_base_smoke','signfix_sensor_fast_smoke'};cases=struct([]);
for n=1:numel(names)
 d=fullfile(root,'cosim','cases',names{n});addpath(d,'-begin');v=load(fullfile(d,'RAW_TRACE.mat'));
 f=jsondecode(fileread(fullfile(d,'MODEL_FREEZE.json')));x=v.trace;index=@(name)find(strcmp(v.names,name),1);
 state=v.mechanical_state;assert(size(state,1)==size(x,1)&&size(state,2)==6);
 duty=x(:,[index('prefix1_q8'),index('prefix2_q8'),index('steady_q8')])/(32768*256);
 expectedAverage=(.1*duty(:,1)+.1*duty(:,2)+.8*duty(:,3));
 recordedAverage=x(:,index('physical_average_q15_q8'))/(32768*256);
 % Separate TZ prefix and average quantization each contribute less than1Q8.
 % Mixed-sign reversal intervals can therefore differ by less than2Q8.
 averageError=max(abs(expectedAverage-recordedAverage));assert(averageError<2/(32768*256));
 stateError=0;
 for k=1:size(state,1)-1
  step=j280_coupled_interval_mex(state(k,:)',f.model.parameters(:),duty(k,:));
  stateError=max(stateError,max(abs(step(end,:)-state(k+1,:))));
 end
 assert(stateError<1e-13,'Saved physical update does not equal exactly one1ms interval');
 jointError=max(abs(x(:,index('physical_theta_now'))-(state(:,3)-state(:,5))));
 armError=max(abs(x(:,index('physical_position_now'))-state(:,1)));
 assert(jointError==0&&armError==0,'Physical logging/state index mismatch');
 s=struct('case_name',names{n},'samples',size(state,1),'state_transitions',size(state,1)-1, ...
  'mechanical_interval_seconds',.001,'mechanical_step_seconds',.00001, ...
  'max_saved_state_vs_one_interval_error',stateError,'max_weighted_native_duty_average_error',averageError, ...
  'separate_TZ_quantization_bound',2/(32768*256), ...
  'joint_measurement_state_error',jointError,'arm_measurement_state_error',armError, ...
  'scope','Saved own-simulation causal-step replay only, never future measuredPWM to a new controller', ...
  'physical_acceptance',false);
 cases=[cases;s];rmpath(d); %#ok<AGROW>
end
result=struct('status','PASS_EXACT_ONE_MILLISECOND_SAVED_INTERVAL_CHAIN','cases',cases, ...
 'new_closed_loop_runs',0,'PID_changes',false,'mechanical_parameter_changes',false,'physical_acceptance',false);
out=fullfile(root,'cosim','sign_audit','INTERVAL_CHAIN_AUDIT.json');assert(~isfile(out),'Refusing overwrite');
fid=fopen(out,'w');fprintf(fid,'%s\n',jsonencode(result,PrettyPrint=true));fclose(fid);disp(jsonencode(result));
end
