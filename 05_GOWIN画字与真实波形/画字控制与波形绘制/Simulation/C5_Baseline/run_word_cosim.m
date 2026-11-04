function run_word_cosim(stopSeconds,tag)
root=fileparts(mfilename('fullpath'));cd(root);addpath(root);
diary(fullfile(root,'logs',['matlab_' tag '.log']));
load_system('simulink');load_system('modelsimlib');
if ~isfolder(fullfile(root,'sdi_storage')),mkdir(fullfile(root,'sdi_storage'));end
Simulink.sdi.setStorageLocation(fullfile(root,'sdi_storage'));
mdl=['C6_Word_ClosedLoop_' tag];
if bdIsLoaded(mdl),close_system(mdl,0);end
new_system(mdl);
set_param(mdl,'SolverType','Fixed-step','Solver','FixedStepDiscrete','FixedStep','0.005', ...
 'StopTime',num2str(stopSeconds),'ReturnWorkspaceOutputs','on','SaveTime','on', ...
 'TimeSaveName','tout','SignalLogging','off','AlgebraicLoopMsg','error');
hdl=[mdl '/Actual_W1_RTL'];
add_block('modelsimlib/HDL Cosimulation',hdl,'Position',[550 100 840 900]);
inputs={'g_error','g_raw','g_request','e_error','e_raw','e_request'};
fields={'motor','target','actual','tracking','vref','vmeas','state','fault','done','settled','completed','accepted','draw_index'};
outputs=[strcat('g_',fields),strcat('e_',fields)];
paths=cellfun(@(n)['/word_closed_loop/' n],[inputs,outputs],'UniformOutput',false);
signs=[ones(1,6),zeros(1,7)];signs=[signs,signs];
post=sprintf('dataset save sim results/word_%s.wlf; puts {WORD_RUN_FINISHED}',tag);
if strcmp(tag,'full')
 post=[post '; if {[catch {do export_native.do} nativeExportError]} {' ...
  'set ef [open results/native_export_error.txt w]; puts $ef $nativeExportError; close $ef; puts $nativeExportError}'];
end
set_param(hdl,'PortPaths',strjoin(paths,';'),'PortModes',mat2str([ones(1,6),2*ones(1,26)]), ...
 'PortTimes',mat2str([-ones(1,6),.005*ones(1,26)]), ...
 'PortSigns',mat2str([-ones(1,6),signs]),'PortFracLengths',mat2str(zeros(1,32)), ...
 'PortWordLengths',mat2str(-ones(1,32)),'PortHDLWordSizes',mat2str(zeros(1,32)), ...
 'ClockPaths','','ClockModes','[]','ClockTimes','[]','CommLocal','on','CommSharedMemory','off', ...
 'CommPortNumber','4476','CommShowInfo','on','TimingMode','Tick','TimingScaleFactor','1e9', ...
 'RunAutoTimescale','off','AllowDirectFeedthrough','off', ...
 'TclPreSimCommand','puts {WORD_RUN_STARTED actual unmodified C5 PID and planner}', ...
 'TclPostSimCommand',post);
for lane=1:2
 if lane==1,name='GOWIN_Plant';y=170;else,name='EasyStart_Plant';y=600;end
 plant=[mdl '/' name];
 add_block('simulink/User-Defined Functions/Level-2 MATLAB S-Function',plant, ...
  'FunctionName','word_plant','Parameters','12','Position',[100 y 310 y+150]);
 for k=1:3,add_line(mdl,sprintf('%s/%d',name,k),sprintf('Actual_W1_RTL/%d',(lane-1)*3+k),'autorouting','on');end
 for k=1:13
  n=sprintf('Data_%d_%d',lane,k);yy=70+(lane-1)*480+(k-1)*29;
  add_block('simulink/Signal Attributes/Data Type Conversion',[mdl '/' n], ...
   'OutDataTypeStr','double','Position',[900 yy 950 yy+18]);
  add_line(mdl,sprintf('Actual_W1_RTL/%d',(lane-1)*13+k),[n '/1'],'autorouting','on');
 end
 add_line(mdl,sprintf('Data_%d_1/1',lane),[name '/1'],'autorouting','on');
 mux=sprintf('Plant_State_%d',lane);
 add_block('simulink/Signal Routing/Mux',[mdl '/' mux],'Inputs','4','Position',[355 y+180 360 y+290]);
 for k=1:4,add_line(mdl,sprintf('%s/%d',name,k+3),sprintf('%s/%d',mux,k),'autorouting','on');end
 sink=sprintf('Plant_Log_%d',lane);variable=sprintf('plant_%d',lane);
 add_block('simulink/Sinks/To Workspace',[mdl '/' sink],'VariableName',variable,'SaveFormat','Timeseries','Position',[390 y+215 475 y+250]);
 add_line(mdl,[mux '/1'],[sink '/1'],'autorouting','on');
 mux=sprintf('RTL_State_%d',lane);
 add_block('simulink/Signal Routing/Mux',[mdl '/' mux],'Inputs','13','Position',[1000 70+(lane-1)*480 1005 455+(lane-1)*480]);
 for k=1:13,add_line(mdl,sprintf('Data_%d_%d/1',lane,k),sprintf('%s/%d',mux,k),'autorouting','on');end
 sink=sprintf('RTL_Log_%d',lane);variable=sprintf('rtl_%d',lane);
 add_block('simulink/Sinks/To Workspace',[mdl '/' sink],'VariableName',variable,'SaveFormat','Timeseries', ...
  'Position',[1070 210+(lane-1)*480 1190 250+(lane-1)*480]);
 add_line(mdl,[mux '/1'],[sink '/1'],'autorouting','on');
 add_block('simulink/Sinks/Scope',[mdl sprintf('/Scope_%d',lane)],'Position',[1070 280+(lane-1)*480 1130 330+(lane-1)*480]);
 add_line(mdl,sprintf('Data_%d_4/1',lane),sprintf('Scope_%d/1',lane),'autorouting','on');
end
note=Simulink.Annotation(mdl,'Actual C5 RTL in ModelSim;5ms interface,one-sample delay;50MHz computation edges;idle clocks omitted.');note.Position=[100 1060];
note=Simulink.Annotation(mdl,'Supplied Oct27 representative count-domain plant;not identified IP570/Furuta hardware;no ideal encoder following.');note.Position=[100 1105];
set_param(mdl,'Location',[50 50 1450 1050],'ZoomFactor','FitSystem');
save_system(mdl,fullfile(root,[mdl '.slx']));
disp(['WORD_MODEL_READY ' tag]);
try
 out=sim(mdl);
 save(fullfile(root,'results',[tag '.mat']),'out','stopSeconds','fields');
 for lane=1:2
  plant=out.(sprintf('plant_%d',lane));rtl=out.(sprintf('rtl_%d',lane));
  tab=array2table([rtl.Time,plant.Data,rtl.Data], ...
   'VariableNames',[{'time_s','angle_counts','angle_rate','plant_position','plant_velocity'},fields]);
  writetable(tab,fullfile(root,'results',sprintf('%s_lane%d.csv',tag,lane)));
  assert(all(isfinite(tab{:,2:end}),'all'),'Nonfinite plant/RTL data');
  assert(all(abs(tab.motor)<=6900) && all(tab.fault==0),'RTL output/guard failure');
  assert(tab.completed(end)>=floor(stopSeconds/.005)-2,'Missing actual RTL transactions');
  fprintf('LANE%d samples=%d transactions=%d peakAngle=%.6f peakTracking=%.0f state=%d\n', ...
    lane,height(tab),tab.completed(end),max(abs(tab.angle_counts)),max(abs(tab.tracking)),tab.state(end));
 end
 disp(['PASS WORD_RTL_SIMULINK_' upper(tag)]);
catch ex
 fid=fopen(fullfile(root,'results',[tag '_error.txt']),'w');fprintf(fid,'%s',getReport(ex,'extended','hyperlinks','off'));fclose(fid);
 disp(getReport(ex,'extended','hyperlinks','off'));rethrow(ex);
end
diary off;
end

