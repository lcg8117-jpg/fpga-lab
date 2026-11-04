function export_labeled(root)
% Add self-contained labels to copied native FIGs, without changing data.
models={'sensor_271','actuator_refit'};times=[.889 .887;1.027 1.018];
for k=1:2
 source=fullfile(root,'cosim','comparisons',models{k},'G_BALANCE_COMPARISON.fig');
 out=fullfile(root,'cosim','comparisons_labeled',models{k});assert(~isfolder(out));mkdir(out);
 originalHash=sha(source);f=openfig(source,'invisible');before=snapshot(f);axesList=findall(f,'Type','axes');
 for a=reshape(axesList,1,[])
  name=string(a.Title.String);
  if contains(name,'80-100s tail')
   xlim(a,[80 100]);title(a,'80-100s tail: NOT OBSERVED','Color',[.75 .1 .1]);continue;
  end
  if contains(name,'G NOT COMPLETED'),continue;end
  lines=findall(a,'Type','line');blue=[];orange=[];reference=[];
  for line=reshape(lines,1,[])
   if isnumeric(line.Color)&&norm(line.Color-[.12 .35 .75])<1e-8,blue=line;
   elseif isnumeric(line.Color)&&norm(line.Color-[.85 .25 .12])<1e-8,orange=line;
   elseif isnumeric(line.Color)&&norm(line.Color)<1e-8,reference=line;end
  end
  if ~isempty(blue)&&~isempty(orange)
   handles=[blue orange];labels={'Original V5','ADC64 + fast P'};
   if ~isempty(reference),handles=[handles reference];labels=[labels {'Reference'}];end
   legend(a,handles,labels,'Location','best');
  end
 end
 sgtitle(f,{[models{k} ' | Blue: original V5 | Orange: ADC64 + fast P'], ...
  sprintf('Native speed protection fault 7: original %.3fs, transplant %.3fs | G and 80-100s tail NOT OBSERVED',times(k,1),times(k,2)), ...
  'Conditional coupled model with matched state/datum/noise; no physical acceptance or geometry-improvement claim'},'Interpreter','none');
 after=snapshot(f);assert(isequaln(before,after),'A native plotted data series changed');
 savefig(f,fullfile(out,'G_BALANCE_COMPARISON_LABELED.fig'));
 exportgraphics(f,fullfile(out,'G_BALANCE_COMPARISON_LABELED.png'),'Resolution',160);
 exportgraphics(f,fullfile(out,'G_BALANCE_COMPARISON_LABELED.pdf'),'ContentType','vector');close(f);
 assert(strcmp(originalHash,sha(source)),'Original native FIG changed');
 r=struct('status','PASS_LABEL_EXPORT_NATIVE_CURVES_UNCHANGED','original_fig_sha256',originalHash, ...
  'data_series',numel(before),'curve_values_unchanged',true,'original_fig_unchanged',true, ...
  'plant_run',false,'HDL_run',false,'physical_acceptance',false,'fault_code',7,'original_stop_s',times(k,1),'transplant_stop_s',times(k,2));
 fid=fopen(fullfile(out,'EXPORT_RECEIPT.json'),'w');fprintf(fid,'%s\n',jsonencode(r,PrettyPrint=true));fclose(fid);
end
end
function data=snapshot(f)
data={};axesList=findall(f,'Type','axes');
for a=reshape(axesList,1,[])
 lines=findall(a,'Type','line');for line=reshape(lines,1,[]),data{end+1}={line.XData,line.YData,line.Color};end %#ok<AGROW>
end
end
function result=sha(path)
fid=fopen(path,'rb');assert(fid>=0);closer=onCleanup(@()fclose(fid));bytes=fread(fid,Inf,'*uint8');
md=java.security.MessageDigest.getInstance('SHA-256');md.update(bytes);result=lower(reshape(dec2hex(typecast(md.digest(),'uint8'),2).',1,[]));
end
