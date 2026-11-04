function export_word_results()
root=fileparts(mfilename('fullpath'));cd(root);addpath(root);
diary(fullfile(root,'logs','matlab_export.log'));
summary=struct('scope','Supplied uncalibrated Oct27 count-domain plant;actual unmodified C5 control/planner RTL', ...
 'physical_hardware_validated',false,'sample_s',.005,'interface_delay_samples',1,'python_used',false);
allMetrics=struct([]);
for lane=1:2
 if lane==1,word='GOWIN';else,word='EasyStart';end
 data=jsondecode(fileread(fullfile(root,'data',['Word_' word '.json'])));
 tab=readtable(fullfile(root,'results',sprintf('baseline_lane%d.csv',lane)));
 assert(height(tab)==12001 && all(abs(diff(tab.time_s)-.005)<1e-10));
 assert(all(tab.fault==0) && tab.accepted(end)==1 && tab.completed(end)==12000);
 assert(tab.done(end)==1 && tab.settled(end)==1 && tab.state(end)==0);
 iAck=find(tab.accepted>0,1);origin=tab.actual(iAck);
 first=find(tab.state==6,1);assert(~isempty(first));
 onset=tab.time_s(first);meta=data.word_metadata;lag=round(meta.window_ms/5);
 relative=tab.time_s-onset;
 selection=find(relative>=meta.word_start_ms/1000-1e-8 & relative<meta.word_end_ms/1000-1e-8);
 assert(all(selection>lag));
 xy=[tab.actual(selection),(tab.actual(selection)-tab.actual(selection-lag))*2*pi/1040/(meta.window_ms/1000)];
 planned=[tab.target(selection),(tab.target(selection)-tab.target(selection-lag))*2*pi/1040/(meta.window_ms/1000)];
 ideal=double(data.target_only_xy);ideal(:,1)=ideal(:,1)+origin;
 assert(all(isfinite(xy),'all'));
 % Recompute the supplied plant from logged actual RTL commands.
 replayError=zeros(height(tab)-1,1);
 x=tab{:,{'angle_counts','angle_rate','plant_position','plant_velocity'}};
 for n=1:height(tab)-1
  p=x(n,:)';u=tab.motor(n);
  for k=1:5
   a=80*p(1)+6*u-.5*p(2);aa=-.02*u-p(4);
   p(2)=p(2)+.001*a;p(1)=p(1)+.001*p(2);
   p(4)=p(4)+.001*aa;p(3)=p(3)+.001*p(4);
  end
  replayError(n)=max(abs(p-x(n+1,:)'));
 end
 assert(max(replayError)<1e-7,'Logged plant does not match actual RTL motor feedback');
 measured=array2table([tab.time_s(selection),xy,planned], ...
  'VariableNames',{'time_s','actual_forest_encoder','window_average_rad_s','rtl_reference_counts','rtl_reference_mean_rad_s'});
 writetable(measured,fullfile(root,'results',[word '_measured_word.csv']));
 f=figure('Visible','off','Color','w','Position',[50 50 1250 1160],'Name',[word ' state trajectory']);
 tl=tiledlayout(f,3,1,'TileSpacing','compact','Padding','compact');
 limitsX=[min([ideal(:,1);planned(:,1);xy(:,1)]) max([ideal(:,1);planned(:,1);xy(:,1)])];
 limitsY=[min([ideal(:,2);planned(:,2);xy(:,2)]) max([ideal(:,2);planned(:,2);xy(:,2)])];
 marginX=max(1,diff(limitsX)*.03);marginY=max(1e-5,diff(limitsY)*.07);
 for panel=1:3
  ax=nexttile(tl);
  if panel==1,plot(ax,ideal(:,1),ideal(:,2),'Color',[.45 .45 .45],'LineWidth',1.25);name='Frozen target preview';
  elseif panel==2,plot(ax,planned(:,1),planned(:,2),'Color',[.85 .35 .05],'LineWidth',1.25);name='Actual RTL planner reference';
  else,plot(ax,xy(:,1),xy(:,2),'Color',[.05 .35 .8],'LineWidth',1.25);name='Actual closed-loop encoder response';end
  grid(ax,'on');xlabel(ax,'Forest encoder position (counts)');
  ylabel(ax,sprintf('Trailing %.0f s mean angular velocity (rad/s)',meta.window_ms/1000));
  title(ax,[word ': ' name]);xlim(ax,limitsX+[-marginX marginX]);ylim(ax,limitsY+[-marginY marginY]);
  set(ax,'FontSize',12);pbaspect(ax,[3.5 1 1]);
 end
 title(tl,'ModelSim / Simulink co-simulation; representative model, not physical measurements','FontWeight','normal');
 savefig(f,fullfile(root,'model_exports',[word '_word.fig']));
 exportgraphics(f,fullfile(root,'model_exports',[word '_word.png']),'Resolution',180);
 exportgraphics(f,fullfile(root,'model_exports',[word '_word.pdf']),'ContentType','vector');close(f);
 f=figure('Visible','off','Color','w','Position',[50 50 1250 930],'Name',[word ' closed-loop time traces']);
 tl=tiledlayout(f,4,1,'TileSpacing','compact','Padding','compact');
 ax=nexttile(tl);plot(ax,tab.time_s,tab.angle_counts,'LineWidth',1.15);grid(ax,'on');ylabel(ax,'Angle error (ADC counts)');
 title(ax,[word ': supplied representative plant, actual RTL controller']);
 ax=nexttile(tl);plot(ax,tab.time_s,tab.target,'--','LineWidth',1.1);hold(ax,'on');plot(ax,tab.time_s,tab.actual,'LineWidth',1.1);
 grid(ax,'on');ylabel(ax,'Forest counts');legend(ax,{'RTL reference','Actual model encoder'},'Location','best');
 ax=nexttile(tl);plot(ax,tab.time_s,tab.tracking,'LineWidth',1.1);grid(ax,'on');ylabel(ax,'Tracking error (counts)');
 ax=nexttile(tl);stairs(ax,tab.time_s,tab.motor,'LineWidth',1);grid(ax,'on');ylabel(ax,'RTL motor request');xlabel(ax,'Simulation time (s)');
 savefig(f,fullfile(root,'model_exports',[word '_time_traces.fig']));
 exportgraphics(f,fullfile(root,'model_exports',[word '_time_traces.png']),'Resolution',180);
 exportgraphics(f,fullfile(root,'model_exports',[word '_time_traces.pdf']),'ContentType','vector');close(f);
 m=struct('word',word,'samples',height(tab),'rtl_transactions',tab.completed(end), ...
  'accepted_jobs',tab.accepted(end),'final_state',tab.state(end),'reference_done',logical(tab.done(end)), ...
  'actual_settled',logical(tab.settled(end)),'origin_count',origin,'final_reference',tab.target(end), ...
  'final_actual',tab.actual(end),'maximum_angle_counts',max(abs(tab.angle_counts)), ...
  'maximum_tracking_counts',max(abs(tab.tracking)), ...
  'word_reference_span_counts',max(planned(:,1))-min(planned(:,1)), ...
  'word_actual_span_counts',max(xy(:,1))-min(xy(:,1)), ...
  'word_tracking_rms_counts',sqrt(mean(tab.tracking(selection).^2)), ...
  'maximum_motor_request',max(abs(tab.motor)),'plant_replay_error',max(replayError), ...
  'wave_onset_s',onset,'word_points',size(xy,1),'average_window_s',meta.window_ms/1000, ...
  'word_readability','FAIL under the supplied model: actual trace is not a legible word; no target replacement or output smoothing');
 if lane==1,allMetrics=m;else,allMetrics(lane)=m;end
end
summary.results=allMetrics;
fid=fopen(fullfile(root,'results','word_cosim_metrics.json'),'w');fprintf(fid,'%s',jsonencode(summary,PrettyPrint=true));fclose(fid);
mdl='C6_Word_ClosedLoop_baseline';load_system(fullfile(root,[mdl '.slx']));
for lane=1:2
 for k=1:13
  set_param(sprintf('%s/Data_%d_%d',mdl,lane,k),'ShowName','off');
 end
end
note=Simulink.Annotation(mdl,sprintf(['Supplied plant states: x=[angle error; angle rate; arm position; arm velocity].\n' ...
 'Five semi-implicit 1 ms steps per 5 ms sample: a=80*x1+6*u-0.5*x2; aa=-0.02*u-x4.\n' ...
 'x2+=0.001*a; x1+=0.001*x2; x4+=0.001*aa; x3+=0.001*x4. Initial x=[12;0;0;0].\n' ...
 'u is the actual RTL motor request. Original-kit raw encoder=round(x3*27/26); RTL maps 26/27.']));
note.Position=[100 1150];
set_param(mdl,'PaperOrientation','portrait','PaperType','A3','ZoomFactor','FitSystem');
save_system(mdl,fullfile(root,'C6_Word_ClosedLoop_review.slx'));
mdl='C6_Word_ClosedLoop_review';
print(['-s' mdl],'-dpng','-r180',fullfile(root,'model_exports','Simulink_native_model.png'));
print(['-s' mdl],'-dpdf',fullfile(root,'model_exports','Simulink_native_model.pdf'));
close_system(mdl,0);
disp(summary);disp('MATLAB_NATIVE_EXPORT_AND_CAUSALITY_PASS');
diary off;
end

