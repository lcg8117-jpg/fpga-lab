function make_easy_start_v2()
% Author a single-line INPUT skeleton. Never replace measured output strokes.
root=fileparts(fileparts(mfilename('fullpath')));destination=fullfile(root,'EasyStart_Font2');
for folder={'data','model_exports','logs','results','native_exports'},if ~isfolder(fullfile(destination,folder{1})),mkdir(fullfile(destination,folder{1}));end,end
diary(fullfile(destination,'logs','font_generation.log'));
glyphs={ ...
 [.82 1;.03 1;0 .52;.63 .52;0 .52;0 0;.85 0;1.06 .10], ...
 [0 .10;.2 .55;.55 .64;.73 .48;.64 .12;.36 0;.09 .08;.02 .30;.19 .56;.58 .62;.71 .50;.70 0;1.04 .10], ...
 [0 .10;.65 .60;.43 .68;.13 .55;.18 .40;.55 .30;.68 .15;.50 0;.10 .05;1.04 .10], ...
 [0 .55;.15 .10;.35 0;.60 .20;.72 .62;.65 .15;.48 -.40;.24 -.48;.14 -.25;.58 .05;1.04 .10], ...
 [.85 .90;.55 1.04;.12 .88;.10 .65;.40 .48;.80 .35;.90 .12;.55 -.02;.10 .10;0 .28;.23 .15;1.06 .10], ...
 [.45 .95;.25 0;.30 .50;0 .50;.70 .50;.30 .50;.25 0;1.04 .10], ...
 [0 .10;.2 .55;.55 .64;.73 .48;.64 .12;.36 0;.09 .08;.02 .30;.19 .56;.58 .62;.71 .50;.70 0;1.04 .10], ...
 [0 0;.14 .65;.14 .10;.26 .48;.43 .65;.60 .58;.65 .40;.84 .42;1.04 .10], ...
 [.45 .95;.25 0;.30 .50;0 .50;.70 .50;.30 .50;.25 0;.85 .04;1.04 .18]};
vertices=[];
for k=1:numel(glyphs),g=glyphs{k};g(:,1)=g(:,1)+(k-1)*1.22;vertices=[vertices;g];end
distance=[0;cumsum(sqrt(sum(diff(vertices).^2,2)))];
dense=pchip(distance,vertices',linspace(0,distance(end),6001))';
% Fix these dimensions before any closed-loop run. Increased X separation,
% explicit crossbars and descenders are properties of the INPUT only.
x=(dense(:,1)-(max(dense(:,1))+min(dense(:,1)))/2)*160/(max(dense(:,1))-min(dense(:,1)))+10;
y=dense(:,2)*48/(max(dense(:,2))-min(dense(:,2)));
past=x-y;
ticks=195;seek=35;gap=45;ret=30;lag=ticks+gap;
arc=[0;cumsum(max(abs(diff(x)),abs(diff(past))))];
[arc,uniqueIndex]=unique(arc,'stable');x=x(uniqueIndex);past=past(uniqueIndex);
at=linspace(0,arc(end),ticks+1)';x=interp1(arc,x,at);past=interp1(arc,past,at);
planned=[zeros(4,1);bridge(0,past(1),seek);past(1:end-1);bridge(past(end),x(1),gap);x(1:end-1);bridge(x(end),0,ret);zeros(4,1)];
smooth=planned;
for pass=1:4,smooth=conv([repmat(smooth(1),2,1);smooth;repmat(smooth(end),2,1)],[1;4;6;4;1]/16,'valid');end
points=quantize(smooth);n=numel(points);validate(points);
q=clocked(points);start=(4+seek+ticks+gap-1)*200;finish=start+ticks*200;window=lag*200;
selected=(start/5:finish/5-1)'+1;delay=window/5;
preview=[q(selected)/65536,(q(selected)-q(selected-delay))/65536*2*pi/1040/(window/1000)];
previous=jsondecode(fileread(fullfile(root,'Final','data','Word_EasyStart.json')));
meta=previous.word_metadata;meta.font_revision='EasyStart_SingleStroke_R2';meta.span_count=160;
meta.geometry_y_span_count=48;meta.axes='X=actual Forest ENC; Y=(ENC(t)-ENC(t-window))*2*pi/1040/window_s';
meta.input_time_allocation='101s total including longer bounded input bridges; authored E/a/s/y/S/t/a/r/t skeleton';
meta.total_s=(n-3)*.2;meta.pre_motion_s=start/1000;meta.word_s=ticks*.2;
meta.window_ms=window;meta.word_start_ms=start;meta.word_end_ms=finish;
meta.point_count=n;meta.travel_count=sum(abs(diff(points)))/16;
meta.executed_reference_span_count=max(preview(:,1))-min(preview(:,1));
meta.checksum=double(programChecksum(points));meta.input_binomial_passes=4;
meta.evidence='Revised INPUT font only; actual RTL/Simulink validation required';
program=struct('format','J280DrawC4Q4','sample_ms',100,'tracking_mode',1,'points',points', ...
 'word_metadata',meta,'target_only_xy',preview);
assert(meta.total_s<=102 && n<=512,'Declared time/capacity exceeded');
for file={fullfile(destination,'data','Word_EasyStart.json'),fullfile(root,'Source','examples','Word_EasyStart.json'),fullfile(root,'Source','host','easy_start_plan.json')}
 f=fopen(file{1},'w');fprintf(f,'%s',jsonencode(program));fclose(f);
end
f=fopen(fullfile(destination,'data','EasyStart.hex'),'w');fprintf(f,'%04X\n',mod(points,65536));fclose(f);
f=fopen(fullfile(destination,'data','word_constants.vh'),'w');
g=jsondecode(fileread(fullfile(root,'Final','data','Word_GOWIN.json')));
fprintf(f,'`define GOWIN_POINTS %d\n`define GOWIN_CHECKSUM 32''h%s\n',numel(g.points),dec2hex(g.word_metadata.checksum,8));
fprintf(f,'`define EASYSTART_POINTS %d\n`define EASYSTART_CHECKSUM 32''h%s\n',n,dec2hex(meta.checksum,8));fclose(f);
copyfile(fullfile(root,'Final','data','GOWIN.hex'),fullfile(destination,'data','GOWIN.hex'));
copyfile(fullfile(root,'Final','data','Word_GOWIN.json'),fullfile(destination,'data','Word_GOWIN.json'));
fig=figure('Visible','off','Color','w','Position',[80 80 1500 700]);
tiledlayout(2,1,'TileSpacing','compact');
ax=nexttile;plot(ax,dense(:,1),dense(:,2),'LineWidth',1.3);axis(ax,'equal');grid(ax,'on');title(ax,'Authored EasyStart INPUT skeleton; not a simulated or measured response');
ax=nexttile;plot(ax,preview(:,1),preview(:,2),'LineWidth',1.3);grid(ax,'on');pbaspect(ax,[5 1 1]);
title(ax,'Exact clocked Q16 reference preview, before closed-loop execution');xlabel(ax,'Forest position counts');ylabel(ax,'Trailing44.8s mean rad/s');
savefig(fig,fullfile(destination,'model_exports','EasyStart_input_preview.fig'));
exportgraphics(fig,fullfile(destination,'model_exports','EasyStart_input_preview.png'),'Resolution',160);close(fig);
fprintf('FONT_INPUT_READY points=%d total=%.1fs travel=%.3f span=%.3f checksum=%s\n',n,meta.total_s,meta.travel_count,meta.executed_reference_span_count,dec2hex(meta.checksum,8));
diary off;
end

function values=bridge(a,b,n)
z=(0:n-1)'/n;values=a+(b-a)*(10*z.^3-15*z.^4+6*z.^5);
end
function points=quantize(values)
states=[0 0 0];costs=0;parents=cell(numel(values),1);positions=parents;
for i=1:numel(values)
 target=values(i)*16;
 if i<=4 || i>numel(values)-4,candidates=0;else,candidates=ceil(target-2):floor(target+2);end
 records=[];
 for p=candidates
  delta=p-states(:,1);second=delta-states(:,2);third=second-states(:,3);
  legal=abs(p)<=3072 & abs(delta)<=96 & abs(second)<=32 & abs(third)<=16;
  ids=find(legal);records=[records;repmat(p,numel(ids),1),delta(ids),second(ids),costs(ids)+(p-target)^2,ids];
 end
 assert(~isempty(records),'Q4 projection infeasible at node%d',i);
 records=sortrows(records,[1 2 3 4 5]);[~,ids]=unique(records(:,1:3),'rows','stable');records=records(ids,:);
 records=sortrows(records,[4 1 2 3]);records=records(1:min(256,size(records,1)),:);
 states=records(:,1:3);costs=records(:,4);parents{i}=records(:,5);positions{i}=records(:,1);
end
[~,id]=min(costs);points=zeros(numel(values),1);
for i=numel(values):-1:1,points(i)=positions{i}(id);id=parents{i}(id);end
end
function validate(p)
d=diff([0;p]);dd=diff([0;d]);ddd=diff([0;dd]);
assert(all(abs(p)<=3072) && all(abs(d)<=96) && all(abs(dd)<=32) && all(abs(ddd)<=16),'Original Q4 input guards');
assert(all(p(1:4)==0) && all(p(end-3:end)==0) && sum(abs(diff(p)))<=1040*16,'Original endpoint/travel guards');
end
function q=clocked(p)
q=0;j=(1:40)';
for s=1:numel(p)-3
 v=p(s:s+3);a=v(1)+4*v(2)+v(3);b=3*(v(3)-v(1));c=3*(v(1)-2*v(2)+v(3));d=-v(1)+3*v(2)-3*v(3)+v(4);
 q=[q;round((a*64000+b*1600*j+c*40*j.^2+d*j.^3)*65536/6144000)];
end
end
function sum=programChecksum(p)
sum=bitxor(bitxor(uint32(hex2dec('6a09e667')),uint32(numel(p))),uint32(1));
for k=1:numel(p)
 word=bitor(bitshift(uint32(k-1),16),uint32(mod(p(k),65536)));
 sum=bitxor(bitor(bitshift(sum,1),bitshift(sum,-31)),word);
end
end
