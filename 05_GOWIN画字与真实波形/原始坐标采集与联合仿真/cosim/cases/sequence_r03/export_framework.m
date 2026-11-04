function receipt=export_framework(caseDir,outDir)
% Export a native Simulink diagram from a completed case, using only a copy.
% No simulation, HDL session, hardware connection, or original-file save runs.
arguments
 caseDir (1,:) char
 outDir (1,:) char
end
caseDir=char(java.io.File(caseDir).getCanonicalPath());
outDir=char(java.io.File(outDir).getCanonicalPath());
assert(isfolder(caseDir),'Case directory does not exist');
assert(~startsWith([outDir filesep],[caseDir filesep],'IgnoreCase',true),'Output must be outside the frozen case');
if ~isfolder(outDir),mkdir(outDir);end
original=fullfile(caseDir,'V5_Current_Field_Loop.slx');
protected={'V5_Current_Field_Loop.slx','RAW_TRACE.mat','actual_rtl.wlf','run_case.m','MODEL_FREEZE.json'};
before=cellfun(@(name)fileHash(fullfile(caseDir,name)),protected,'UniformOutput',false);
execution=jsondecode(fileread(fullfile(caseDir,'EXECUTION_RECEIPT.json')));
assert(strcmp(execution.status,'PASS_EXECUTION_ONLY'),'Only completed native cases may be exported');
[~,caseName]=fileparts(caseDir);copyName=matlab.lang.makeValidName(['Framework_' caseName]);
copyPath=fullfile(outDir,[copyName '.slx']);
assert(~isfile(copyPath),'Refusing to overwrite an exported model copy');
previous=pwd;restore=onCleanup(@()cd(previous)); %#ok<NASGU>
cd(outDir);
Simulink.fileGenControl('set','CacheFolder',fullfile(outDir,'cache'),'CodeGenFolder',fullfile(outDir,'codegen'),'createDir',true);
copyfile(original,copyPath);
load_system(copyPath);
cleanup=onCleanup(@()close_system(copyName,0)); %#ok<NASGU>
Simulink.BlockDiagram.arrangeSystem(copyName);
set_param(copyName,'ZoomFactor','FitSystem','PaperOrientation','landscape','PaperType','A3','PaperPositionMode','auto');
save_system(copyName,copyPath);
pngPath=fullfile(outDir,[copyName '.png']);pdfPath=fullfile(outDir,[copyName '.pdf']);
print(['-s' copyName],'-dpng','-r180',pngPath);
print(['-s' copyName],'-dpdf','-bestfit',pdfPath);
after=cellfun(@(name)fileHash(fullfile(caseDir,name)),protected,'UniformOutput',false);
assert(isequal(before,after),'Original case artifacts changed during export');
receipt=struct('status','PASS_NATIVE_FRAMEWORK_EXPORT','case_name',caseName, ...
 'scope','Native Simulink diagram export of an arranged copy; no simulation or measured-output substitution', ...
 'original_files',{protected},'original_sha256_before',{before},'original_sha256_after',{after}, ...
 'original_files_unchanged',true,'copy_slx',copyPath,'png',pngPath,'pdf',pdfPath);
file=fopen(fullfile(outDir,'EXPORT_RECEIPT.json'),'w');assert(file>=0);closer=onCleanup(@()fclose(file)); %#ok<NASGU>
fwrite(file,jsonencode(receipt,PrettyPrint=true),'char');fprintf(file,'\n');
disp(jsonencode(receipt,PrettyPrint=true));
end

function value=fileHash(path)
file=fopen(path,'rb');assert(file>=0,['Missing source file: ' path]);closer=onCleanup(@()fclose(file)); %#ok<NASGU>
bytes=fread(file,Inf,'*uint8');digest=java.security.MessageDigest.getInstance('SHA-256');
digest.update(typecast(bytes,'int8'));value=lower(reshape(dec2hex(typecast(digest.digest(),'uint8'),2)',1,[]));
end
