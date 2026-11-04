function p=v5_initialize
% Declared conditional state/datum; never a field calibration.
f=jsondecode(fileread('MODEL_FREEZE.json'));n=load(f.noise_path,'noise');
assert(isfield(f,'physical_adc_datum_q4'),'Explicit independent physical datum required');
assert(isfield(f,'encoder_sign') && ismember(f.encoder_sign,[-1 1]),'Explicit model/encoder coordinate sign required');
p=f;p.parameters=f.model.parameters(:);p.noise=n.noise;p.noise.values=double(p.noise.values);
p.history_time=(-600:0)'*.00001;
assert(numel(p.initial_state)==6 && all(isfinite(p.initial_state)) && ...
 isscalar(p.zero_q4)&&isfinite(p.zero_q4)&&p.zero_q4>=0&&p.zero_q4<16384 && ...
 isscalar(p.physical_adc_datum_q4)&&isfinite(p.physical_adc_datum_q4)&&p.physical_adc_datum_q4>=0&&p.physical_adc_datum_q4<16384);
end
