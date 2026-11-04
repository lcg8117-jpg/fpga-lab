function export_actual_only()
root=fileparts(mfilename('fullpath'));
data=readtable(fullfile(root,'results','EasyStart_measured_word.csv'));
figureHandle=figure('Visible','off','Color','w','Position',[80 80 1500 460]);
plot(data.actual_forest_encoder,data.window_average_rad_s,'Color',[.05 .35 .8],'LineWidth',1.2);
grid on;pbaspect([5 1 1]);
x=[min(data.actual_forest_encoder),max(data.actual_forest_encoder)];
y=[min(data.window_average_rad_s),max(data.window_average_rad_s)];
xlim(x+[-3 3]);ylim(y+[-.1 .12]*diff(y));
xlabel('Actual encoder position (Forest counts)');ylabel('Trailing48s mean velocity (rad/s)');
title('EasyStart R3: actual ModelSim / Simulink closed-loop feedback; no output smoothing');
savefig(figureHandle,fullfile(root,'model_exports','EasyStart_actual_only.fig'));
exportgraphics(figureHandle,fullfile(root,'model_exports','EasyStart_actual_only.png'),'Resolution',160);
exportgraphics(figureHandle,fullfile(root,'model_exports','EasyStart_actual_only.pdf'),'ContentType','vector');close(figureHandle);
disp('FINAL_ACTUAL_FEEDBACK_EXPORT_PASS');
end
