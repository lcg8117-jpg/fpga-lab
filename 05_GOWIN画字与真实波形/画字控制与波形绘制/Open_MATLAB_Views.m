function Open_MATLAB_Views()
root=fileparts(mfilename('fullpath'));
caseRoot=fullfile(root,'Simulation','EasyStart_R3');
addpath(caseRoot);
load_system('simulink');load_system('modelsimlib');
open_system(fullfile(root,'Presentation','C6_Word_ClosedLoop_Capture.slx'));
set_param('C6_Word_ClosedLoop_Capture','ZoomFactor','FitSystem');
names={'GOWIN_word.fig','EasyStart_word.fig','GOWIN_time_traces.fig','EasyStart_time_traces.fig','EasyStart_actual_only.fig'};
for k=1:numel(names)
 f=openfig(fullfile(caseRoot,'model_exports',names{k}),'new','visible');
 set(f,'WindowStyle','docked','Name',erase(names{k},'.fig'),'NumberTitle','off');
end
disp('Saved C6 plots and framework are open. No simulation or hardware operation was started.');
end
