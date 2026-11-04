addpath(fileparts(mfilename('fullpath')));
% Called from its isolated case directory; no old working directory writes.
diary('cosim_matlab.log');load_system('simulink');load_system('modelsimlib');
global BALANCE_PHYSICS BALANCE_STATE_HISTORY
freeze=jsondecode(fileread('MODEL_FREEZE.json'));BALANCE_PHYSICS=v5_initialize;fit=rmfield(BALANCE_PHYSICS,'noise');
Simulink.sdi.setStorageMode('memory');mdl='V5_Current_Field_Loop';new_system(mdl);
set_param(mdl,'SolverType','Fixed-step','Solver','FixedStepDiscrete','FixedStep','.0005', ...
 'StopTime',num2str(freeze.duration_s+.0005),'ReturnWorkspaceOutputs','on','SignalLogging','off','AlgebraicLoopMsg','error');
hdl=[mdl '/Actual_PID_Planner_Actuator'];add_block('modelsimlib/HDL Cosimulation',hdl,'Position',[450 80 750 600]);
inputs={'fast_error','measured_error','measured_raw','request_index','measured_raw_now','measured_guard', ...
 'observation_source_faults','observation_permission','observation_stop','observation_adc_raw','observation_adc_q4', ...
 'raw_ms1','raw_ms2','raw_ms3','raw_ms4','raw_ms5','age_ms1','age_ms2','age_ms3','age_ms4','age_ms5'};
fields={'motor_command','target_position','actual_position','position_error','motion_state','fault', ...
 'trajectory_done','tracking_settled','completed_transactions','accepted_count','draw_index', ...
 'reference_velocity','measured_velocity','virtual_input_error','lead_inhibited','raw_edges','edge_budget', ...
 'physical_average_q15_q8','applied_snapshot_q15','angle_observed','prefix1_q8','prefix2_q8','steady_q8', ...
 'frame_actual','frame_target','guard_observed','outer_fault_code'};
allnames=[inputs,fields];paths=cellfun(@(n)['/v5_cosim_core/' n],allnames,'UniformOutput',false);
signed=[1,1,1,1,zeros(1,7),ones(1,6),1,1,1,ones(1,6),0];assert(numel(signed)==27);
set_param(hdl,'PortPaths',strjoin(paths,';'),'PortModes',mat2str([ones(1,21),2*ones(1,27)]), ...
 'PortTimes',mat2str([-ones(1,21),.0005*ones(1,27)]),'PortSigns',mat2str([-ones(1,21),signed]), ...
 'PortFracLengths',mat2str(zeros(1,48)),'PortWordLengths',mat2str(-ones(1,48)), ...
 'PortHDLWordSizes',mat2str(zeros(1,48)),'ClockPaths','','ClockModes','[]','ClockTimes','[]', ...
 'PreRunTime','3','PreRunTimeUnit','ms','CommLocal','on','CommSharedMemory','off','CommPortNumber',num2str(freeze.port), ...
 'CommShowInfo','off','TimingMode','Tick','TimingScaleFactor','1e9','RunAutoTimescale','off','AllowDirectFeedthrough','on', ...
 'TclPreSimCommand','puts {V5_CURRENT_FIELD_BEGIN}','TclPostSimCommand','puts {V5_CURRENT_FIELD_END}');
plant=[mdl '/Physical_Plant_And_Source_Observation'];
add_block('simulink/User-Defined Functions/Level-2 MATLAB S-Function',plant,'FunctionName','v5_plant','Position',[90 150 300 500]);
inputTypes={'int16','int16','int32','uint32','int32','int16','fixdt(0,2,0)','boolean','boolean', ...
 'fixdt(0,10,0)','fixdt(0,14,0)','int32','int32','int32','int32','int32','uint32','uint32','uint32','uint32','uint32'};
assert(numel(inputTypes)==21);
plantPorts=[25,1:5,10:24];
for k=1:21
 name=sprintf('Input_Exact_Type_%d',k);
 add_block('simulink/Signal Attributes/Data Type Conversion',[mdl '/' name], ...
  'OutDataTypeStr',inputTypes{k},'Position',[340 40+k*26 410 58+k*26]);
 add_line(mdl,sprintf('Physical_Plant_And_Source_Observation/%d',plantPorts(k)),[name '/1'],'autorouting','on');
 add_line(mdl,[name '/1'],sprintf('Actual_PID_Planner_Actuator/%d',k),'autorouting','on');
end
for k=1:27
 name=sprintf('RTL_Double_%d',k);yy=40+(k-1)*28;
 add_block('simulink/Signal Attributes/Data Type Conversion',[mdl '/' name],'OutDataTypeStr','double','Position',[800 yy 855 yy+18]);
 add_line(mdl,sprintf('Actual_PID_Planner_Actuator/%d',k),[name '/1'],'autorouting','on');
end
for q=1:3,add_line(mdl,sprintf('RTL_Double_%d/1',q+20),sprintf('Physical_Plant_And_Source_Observation/%d',q),'autorouting','on');end
add_line(mdl,'RTL_Double_9/1','Physical_Plant_And_Source_Observation/4');add_line(mdl,'RTL_Double_20/1','Physical_Plant_And_Source_Observation/5');
add_block('simulink/Signal Routing/Mux',[mdl '/RTL_Mux'],'Inputs','27','Position',[900 40 905 790]);
for k=1:27,add_line(mdl,sprintf('RTL_Double_%d/1',k),sprintf('RTL_Mux/%d',k),'autorouting','on');end
add_block('simulink/Sinks/To Workspace',[mdl '/RTL_Log'],'VariableName','rtl_gate','SaveFormat','Timeseries','Position',[960 250 1090 280]);add_line(mdl,'RTL_Mux/1','RTL_Log/1');
add_block('simulink/Signal Routing/Mux',[mdl '/Plant_Mux'],'Inputs','4','Position',[330 420 335 540]);
for k=1:4,add_line(mdl,sprintf('Physical_Plant_And_Source_Observation/%d',k+5),sprintf('Plant_Mux/%d',k),'autorouting','on');end
add_block('simulink/Sinks/To Workspace',[mdl '/Plant_Log'],'VariableName','plant_gate','SaveFormat','Timeseries','Position',[380 460 490 500]);add_line(mdl,'Plant_Mux/1','Plant_Log/1');
add_block('simulink/Sinks/Stop Simulation',[mdl '/Stop_At_Native_Fault'],'Position',[950 850 1080 890]);add_line(mdl,'RTL_Double_6/1','Stop_At_Native_Fault/1');
save_system(mdl,fullfile(pwd,[mdl '.slx']));
try
 out=sim(mdl);rtl=out.rtl_gate;state=out.plant_gate;collect=mod(round(rtl.Time/.0005),2)==1;
 physicalTime=rtl.Time(collect)-.0005;
 assert(all(rtl.Data(collect,9)==round(physicalTime/.001)+1),'Transaction phase mismatch');
 assert(all(rtl.Data(collect,20)==round(state.Data(collect,4))),'Sensor causality mismatch');
 tab=array2table([physicalTime,state.Data(collect,:),rtl.Data(collect,:)],'VariableNames', ...
  [{'time_s','physical_theta_now','physical_position_now','physical_arm_rate','observed_angle_state'},fields]);
 trace=table2array(tab);names=tab.Properties.VariableNames;
 wave=find(tab.motion_state==6,1);if isempty(wave),freeze.sim_first_WAVE=[];else,freeze.sim_first_WAVE=tab.time_s(wave);end
 mechanical_state=BALANCE_STATE_HISTORY(1:height(tab),:);mechanical_state_time=physicalTime; save('RAW_TRACE.mat','trace','names','fit','freeze','mechanical_state','mechanical_state_time');
 writetable(tab,'FULL_1KHZ.csv');
 outcome=struct('status','EXECUTED_CONDITIONAL_CASE_NOT_ACCEPTANCE','requested_duration_s',freeze.duration_s, ...
  'executed_samples',height(tab),'last_time_s',tab.time_s(end),'native_fault_stop',any(tab.fault), ...
  'last_outer_fault',tab.outer_fault_code(end),'first_WAVE_s',freeze.sim_first_WAVE, ...
  'complete_sequence_and_tail',~isempty(wave)&&tab.time_s(end)-tab.time_s(wave)>=119.390000,'physical_acceptance',false);
 fid=fopen('CASE_OUTCOME.json','w');fprintf(fid,'%s\n',jsonencode(outcome,PrettyPrint=true));fclose(fid);
 fprintf('COMPLETED_CURRENT_FIELD_PIPELINE samples=%d last=%d fault=%d\n',height(tab),tab.completed_transactions(end),tab.fault(end));
catch ex
 fid=fopen('case_error.txt','w');fprintf(fid,'%s',getReport(ex,'extended','hyperlinks','off'));fclose(fid);
 disp(getReport(ex,'extended','hyperlinks','off'));diary off;rethrow(ex);
end
diary off;
