function export_native(root,caseName,outDir)
% Export native closed-loop measurements without fitting or output transforms.
if nargin<2,caseName='sequence_r03';end
caseDir=fullfile(root,'cosim','cases',caseName);
if nargin<3,outDir=fullfile(root,'results');end
if ~exist(outDir,'dir'),mkdir(outDir);end
plan=jsondecode(fileread(fullfile(caseDir,'TRIAL_INPUT.json')));
raw=readtable(fullfile(caseDir,'FULL_1KHZ.csv'));
meta=plan.word_metadata;window=meta.window_ms/1000;
first=find(raw.motion_state==6,1);assert(~isempty(first),'No native WAVE was observed');
origin=0;onset=raw.time_s(first);t=raw.time_s-onset;
keep=abs(t*200-round(t*200))<1e-7 & t>=0;
s=raw(keep,:);tm=round((s.time_s-onset)*1000);enc=s.frame_actual;
assert(all(diff(tm)==5),'Native capture is not the expected exact grid');
lag=meta.window_ms/5;indices=(lag+1:height(s))';
x=enc(indices)-origin;
y=(enc(indices)-enc(indices-lag))*2*pi/(1040*window);
relative=tm(indices);write=relative>=meta.word_start_ms & relative<meta.word_end_ms;
target=double(plan.target_only_xy);
assert(sum(write)==size(target,1),'Not all writing samples exist');
phase=strings(numel(relative),1);phases=plan.sequence_metadata.phases;
for k=1:numel(phases)
    active=relative>=phases(k).start_ms & relative<phases(k).end_ms;
    phase(active)=string(phases(k).name);
end
xy=table(relative,x,y,phase,'VariableNames',{'relative_ms','actual_x_count','actual_y_rad_s','phase'});
writetable(xy,fullfile(outDir,'NATIVE_MEASURED_XY.csv'));
scale=2*pi/(1040*window);distance=hypot(x(write)-target(:,1),(y(write)-target(:,2))/scale);
ordered=sort(distance);p95=ordered(max(1,ceil(.95*numel(ordered))));
tail=t>=meta.total_s & t<=meta.total_s+20;
writeAll=t>=meta.word_start_ms/1000 & t<meta.word_end_ms/1000;
rmsValue=@(a)sqrt(mean(a.^2));
metric=struct('status','CONDITIONAL_MODEL_NOT_PHYSICAL_ACCEPTANCE', ...
 'sample_count',height(raw),'onset_s',onset,'actual_origin_count',origin, ...
 'writing_points',sum(write),'writing_expected_points',size(target,1), ...
 'x_rms_count',rmsValue(x(write)-target(:,1)), ...
 'y_rms_rad_s',rmsValue(y(write)-target(:,2)), ...
 'paired_xy_rms_canonical_count',rmsValue(distance),'paired_xy_p95_canonical_count',p95, ...
 'tracking_rms_count',rmsValue(raw.frame_actual(writeAll)-raw.frame_target(writeAll)), ...
 'tail_position_p2p_count',max(raw.frame_actual(tail))-min(raw.frame_actual(tail)), ...
 'tail_position_rms_count',rmsValue(raw.frame_actual(tail)-origin), ...
 'maximum_measured_joint_angle_rad',max(abs(raw.physical_theta_now)), ...
 'maximum_pin_snapshot_q15',max(abs(raw.applied_snapshot_q15)), ...
 'maximum_actual_position_count',max(raw.frame_actual),'minimum_actual_position_count',min(raw.frame_actual), ...
 'last_native_strict_settled',logical(raw.tracking_settled(end)), ...
 'native_fault',logical(any(raw.fault)),'physical_acceptance',false, ...
 'wave_duration_s',t(end),'window_s',window, ...
 'thresholds_fixed_before_result',true, ...
 'paired_xy_rms_limit',6.72,'paired_xy_p95_limit',13.44);
metric.geometry_numeric_gate=metric.paired_xy_rms_canonical_count<=6.72 && p95<=13.44;
metric.closed_loop_numeric_gate=metric.geometry_numeric_gate && ~metric.native_fault && metric.last_native_strict_settled;
metric.approved_dense_loop_style_acceptance=false;
metric.full_requirement_acceptance=false;
fid=fopen(fullfile(outDir,'NATIVE_METRICS.json'),'w');fprintf(fid,'%s\n',jsonencode(metric,PrettyPrint=true));fclose(fid);

f=figure('Visible','off','Position',[100 100 1600 690],'Color','w');
tiledlayout(1,2,'TileSpacing','compact');
nexttile;plot(target(:,1),target(:,2),'-','Color',[.45 .45 .45],'LineWidth',1.2);grid on;
title('Executed reference only — no measured samples');xlabel('ENC reference (count)');ylabel('46 s average speed (rad/s)');
xlim([-160 160]);ylim([-64 64]*scale);
nexttile;hold on;
plot(x(write),y(write),'-','Color',[.65 .65 .65],'LineWidth',.6,'HandleVisibility','off');
colors=lines(5);letters='GOWIN';
for k=1:numel(phases)
    rows=write & phase==string(phases(k).name);
    if ~any(rows),continue;end
    index=find(strcmp({'G','O','W','I','N'},phases(k).name),1);
    if isempty(index),color=[.5 .5 .5];else,color=colors(index,:);end
    plot(x(rows),y(rows),'-','Color',color,'LineWidth',.8,'DisplayName',phases(k).name);
end
grid on;xlim([-160 160]);ylim([-64 64]*scale);xlabel('Measured ENC - origin (count)');ylabel('Measured 46 s average speed (rad/s)');
title('Native RTL + conditional plant feedback');legend('Location','southoutside','NumColumns',5,'Interpreter','none');
sgtitle('Sequential GOWIN — same global axes; all writing samples included');
savefig(f,fullfile(outDir,'REFERENCE_VS_NATIVE.fig'));
exportgraphics(f,fullfile(outDir,'REFERENCE_VS_NATIVE.png'),'Resolution',160);
exportgraphics(f,fullfile(outDir,'REFERENCE_VS_NATIVE.pdf'),'ContentType','vector');close(f);

f=figure('Visible','off','Position',[100 100 1500 1050],'Color','w');
tiledlayout(5,1,'TileSpacing','compact');
nexttile;plot(t,raw.frame_target,'Color',[.5 .5 .5]);hold on;plot(t,raw.frame_actual,'Color',[.1 .4 .8]);ylabel('ENC (count)');grid on;legend('Reference','Measured');
nexttile;plot(t,raw.physical_theta_now*180/pi,'Color',[.15 .5 .25]);ylabel('Joint angle (deg)');grid on;
nexttile;plot(t,raw.applied_snapshot_q15/32768,'Color',[.7 .15 .25]);ylabel('Pin duty');grid on;
nexttile;stairs(t,raw.motion_state);hold on;stairs(t,raw.tracking_settled);ylabel('Motion state');grid on;legend('Native state','Strict settled');
nexttile;phaseIndex=zeros(size(t));
for k=1:numel(phases)
    phaseIndex(t*1000>=phases(k).start_ms & (t*1000<phases(k).end_ms | ...
        (k==numel(phases) & t*1000==phases(k).end_ms)))=k;
end
stairs(t,phaseIndex);yticks(1:numel(phases));yticklabels({phases.name});set(gca,'TickLabelInterpreter','none');ylabel('Phase');xlabel('Time since first native WAVE (s)');grid on;
sgtitle('Complete measured dynamics — unchanged PID / angle loop / protection');
savefig(f,fullfile(outDir,'COMPLETE_NATIVE_DYNAMICS.fig'));
exportgraphics(f,fullfile(outDir,'COMPLETE_NATIVE_DYNAMICS.png'),'Resolution',150);
exportgraphics(f,fullfile(outDir,'COMPLETE_NATIVE_DYNAMICS.pdf'),'ContentType','vector');close(f);
copyfile(fullfile(caseDir,'V5_Current_Field_Loop.slx'),fullfile(outDir,'V5_Sequential_Conditional_Loop.slx'));
load_system(fullfile(outDir,'V5_Sequential_Conditional_Loop.slx'));
Simulink.BlockDiagram.arrangeSystem('V5_Sequential_Conditional_Loop');
set_param('V5_Sequential_Conditional_Loop/Actual_PID_Planner_Actuator','CommShowInfo','off');
properties=get_param('V5_Sequential_Conditional_Loop','ObjectParameters');
for name={'PaperPrintName','PaperPrintDate','PaperPrintTime'}
    if isfield(properties,name{1}),set_param('V5_Sequential_Conditional_Loop',name{1},'off');end
end
set_param('V5_Sequential_Conditional_Loop','PaperPositionMode','auto','PaperOrientation','landscape', ...
 'PaperType','A3','ShowPageBoundaries','off');
print('-sV5_Sequential_Conditional_Loop','-dpng','-r140',fullfile(outDir,'SIMULINK_FRAMEWORK.png'));
print('-sV5_Sequential_Conditional_Loop','-dpdf','-bestfit',fullfile(outDir,'SIMULINK_FRAMEWORK.pdf'));
save_system('V5_Sequential_Conditional_Loop',fullfile(outDir,'V5_Sequential_Conditional_Loop.slx'));
close_system('V5_Sequential_Conditional_Loop',0);
fprintf('EXPORTED_NATIVE_MEASUREMENTS points=%d numeric_gate=%d final_settled=%d\n',sum(write),metric.closed_loop_numeric_gate,metric.last_native_strict_settled);
end
