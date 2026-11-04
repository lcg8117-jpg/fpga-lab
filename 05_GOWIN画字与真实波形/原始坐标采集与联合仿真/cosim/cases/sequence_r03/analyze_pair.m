function result=analyze_pair(baseDir,newDir,out)
% Fixed-target metrics on actual native sampled feedback; no output fitting.
assert(~isfolder(out),'Use a fresh comparison directory');mkdir(out);
dirs={baseDir,newDir};C=cell(2,1);S=struct([]);factor=2*pi/1040/36;
for k=1:2
 d=dirs{k};v=load(fullfile(d,'RAW_TRACE.mat'));f=jsondecode(fileread(fullfile(d,'MODEL_FREEZE.json')));
 p=jsondecode(fileread(fullfile(d,'TRIAL_INPUT.json')));T=array2table(v.trace,'VariableNames',v.names);
 wave=find(T.motion_state==6,1);rel=nan(height(T),1);complete=false;xy=[];sel=[];past=[];
 if ~isempty(wave)
  rel=round((T.time_s-T.time_s(wave))*1000);sel=find(rel>=42000 & rel<72000 & mod(rel,5)==0);past=sel-36000;
  complete=numel(sel)==6000 && all(past>0) && max(rel)>=99995;
 end
 s=struct('case_name',f.case_name,'model',f.model.name,'profile',f.profile,'physical_acceptance',false, ...
  'executed_samples',height(T),'first_WAVE_s',[],'last_time_s',T.time_s(end),'complete_G_and_original_80_100_tail',complete, ...
  'fault_samples',nnz(T.fault),'max_outer_fault',max(T.outer_fault_code), ...
  'max_abs_measured_joint_angle_rad',max(abs(T.physical_theta_now)), ...
  'max_abs_command',max(abs(T.motor_command)),'final_reference',T.frame_target(end), ...
  'final_actual',T.frame_actual(end),'strict_settled',logical(T.tracking_settled(end)));
 for name={'x_RMS_count','y_RMS_rad_s','x_mean_count','y_mean_rad_s','tracking_RMS_count','outside_original_view', ...
  'tail_80_100_RMS_count','tail_80_100_p2p_count','tail_80_100_all_settled','final_continuous_settled_span_s'},s.(name{1})=[];end
 if ~isempty(wave),s.first_WAVE_s=T.time_s(wave);end
 if complete
  xy=[T.frame_actual(sel),(T.frame_actual(sel)-T.frame_actual(past))*factor];e=xy-p.target_only_xy;
  tail=find(rel>=80000 & rel<100000 & mod(rel,5)==0);assert(numel(tail)==4000);
  s.x_RMS_count=sqrt(mean(e(:,1).^2));s.y_RMS_rad_s=sqrt(mean(e(:,2).^2));
  s.x_mean_count=mean(e(:,1));s.y_mean_rad_s=mean(e(:,2));
  s.tracking_RMS_count=sqrt(mean((T.frame_actual(sel)-T.frame_target(sel)).^2));
  s.outside_original_view=nnz(abs(xy(:,1))>60 | abs(xy(:,2))>60*factor);
  s.tail_80_100_RMS_count=sqrt(mean((T.frame_actual(tail)-T.frame_target(tail)).^2));
  s.tail_80_100_p2p_count=range(T.frame_actual(tail));
  s.tail_80_100_all_settled=all(T.tracking_settled(tail) & T.trajectory_done(tail));
  finalValid=logical(T.tracking_settled & T.trajectory_done);j=find(~finalValid,1,'last');
  s.final_continuous_settled_span_s=0;if finalValid(end),if isempty(j),j=0;end;s.final_continuous_settled_span_s=(height(T)-j-1)*.001;end
  X=table(rel(sel),p.target_only_xy(:,1),p.target_only_xy(:,2),xy(:,1),xy(:,2),e(:,1),e(:,2), ...
   'VariableNames',{'relative_ms','fixed_x_count','fixed_y_rad_s','actual_x_count','actual_y_rad_s','x_error_count','y_error_rad_s'});
  writetable(X,fullfile(out,[f.profile '_WRITING.csv']));
 end
 S=[S;s];C{k}=struct('tab',T,'relative',rel,'xy',xy,'plan',p,'freeze',f,'state',v.mechanical_state); %#ok<AGROW>
end
a=C{1}.freeze;b=C{2}.freeze;
assert(isequal(a.model,b.model)&&a.zero_q4==b.zero_q4&&a.physical_adc_datum_q4==b.physical_adc_datum_q4 && ...
 isequal(a.initial_state,b.initial_state)&&strcmp(a.noise_sha256,b.noise_sha256)&&strcmp(a.input_plan_sha256,b.input_plan_sha256),'Unmatched condition');
checks=struct('complete',all([S.complete_G_and_original_80_100_tail]),'both_axes_improve',false,'fault_free',all([S.fault_samples]==0 & [S.max_outer_fault]==0), ...
 'outside_not_worse',false,'tail_RMS_not_worse',false,'tail_p2p_not_worse',false,'reference_returned_zero',S(2).final_reference==0);
if checks.complete
 checks.both_axes_improve=S(2).x_RMS_count<S(1).x_RMS_count&&S(2).y_RMS_rad_s<S(1).y_RMS_rad_s;
 checks.outside_not_worse=S(2).outside_original_view<=S(1).outside_original_view;
 checks.tail_RMS_not_worse=S(2).tail_80_100_RMS_count<=S(1).tail_80_100_RMS_count;
 checks.tail_p2p_not_worse=S(2).tail_80_100_p2p_count<=S(1).tail_80_100_p2p_count;
end
result=struct('status','CONDITIONAL_PAIRED_COMPARISON','summaries',S,'checks',checks, ...
 'passes_original_geometry_gate',all(structfun(@logical,checks)),'physical_acceptance',false);
fid=fopen(fullfile(out,'COMPARISON_RESULT.json'),'w');fprintf(fid,'%s\n',jsonencode(result,PrettyPrint=true));fclose(fid);
colors={[.12 .35 .75],[.85 .25 .12]};fig=figure('Visible','off','Color','w','Position',[30 30 1700 1000]);tiledlayout(2,3,'Padding','compact');
nexttile;plot(C{1}.plan.target_only_xy(:,1),C{1}.plan.target_only_xy(:,2),'k','LineWidth',1);hold on;
for k=1:2,if ~isempty(C{k}.xy),plot(C{k}.xy(:,1),C{k}.xy(:,2),'Color',colors{k});end;end
grid on;xlabel('Encoder count');ylabel('36s causal velocity rad/s');
if checks.complete,title('Fixed G and raw measured feedback');legend('Fixed target','Original balance','ADC64 + fast P');
else,title('G NOT COMPLETED: reference target only','Color',[.75 .1 .1]);legend('Reference target, not executed feedback');end
nexttile;hold on;for k=1:2,T=C{k}.tab;plot(T.time_s,T.frame_actual,'Color',colors{k});end
plot(C{1}.tab.time_s,C{1}.tab.frame_target,'k--');grid on;xlabel('Physical time s');ylabel('Count');title('Reference and acquired position');
nexttile;hold on;for k=1:2,T=C{k}.tab;plot(T.time_s,T.frame_actual-T.frame_target,'Color',colors{k});end
grid on;xlabel('Physical time s');ylabel('Tracking error count');title('Original encoder error, no smoothing');
nexttile;hold on;for k=1:2,T=C{k}.tab;plot(T.time_s,T.physical_theta_now*180/pi,'Color',colors{k});end
grid on;xlabel('Physical time s');ylabel('Measured joint angle deg');title('Pendulum relative to support');
nexttile;hold on;for k=1:2,T=C{k}.tab;plot(T.time_s,T.physical_average_q15_q8/(256*32768),'Color',colors{k});end
grid on;xlabel('Physical time s');ylabel('Signed actual pin duty');title('Native PWM drive');
nexttile;hold on;for k=1:2,T=C{k}.tab;mask=C{k}.relative>=80000;plot(C{k}.relative(mask)/1000,T.frame_actual(mask),'Color',colors{k});end
grid on;xlabel('Time after WAVE s');ylabel('Count');title('Original 80-100s tail, actual feedback');
if ~checks.complete,text(.5,.5,'80-100s tail not recorded; no settling claim','Units','normalized','HorizontalAlignment','center','Color',[.75 .1 .1]);end
sgtitle({[a.model.name ' | same G, state, datum, calibration and frozen noise'], ...
 'Actual RTL + supplied conditional coupled plant; no physical acceptance or field-calibrated model claim'},'Interpreter','none');
savefig(fig,fullfile(out,'G_BALANCE_COMPARISON.fig'));exportgraphics(fig,fullfile(out,'G_BALANCE_COMPARISON.png'),'Resolution',160);
exportgraphics(fig,fullfile(out,'G_BALANCE_COMPARISON.pdf'),'ContentType','vector');close(fig);
end
