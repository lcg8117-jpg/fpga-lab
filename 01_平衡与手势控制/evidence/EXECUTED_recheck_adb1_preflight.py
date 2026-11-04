"""Retry only the read-only preflight: first run sent zero bytes, no JTAG call."""
import datetime,json,shutil
import restore_adb1_s1 as r
previous=json.loads((r.EVID/'DEPLOYMENT_RESULT.json').read_text('utf-8'))
assert previous['sram_attempts']==0 and previous['uart_transmitted_bytes']==0 and previous['calls']==[]
assert not (r.EVID/'SRAM_ONCE.lock').exists()
saved=r.EVID/'PREFLIGHT_ATTEMPT_1.json';assert not saved.exists()
shutil.copy2(r.EVID/'DEPLOYMENT_RESULT.json',saved)
r.record=previous;r.record.update(status='RECHECKING_FRESH_RX',prior_preflight_record=saved.name)
r.record.pop('error',None)
original=r.capture
r.capture=lambda port,label,seconds,parser_type:original(port,'RECHECK_'+label,seconds,parser_type)
try:r.execute()
except BaseException as exc:
    r.record.update(status='FAILED_OR_UNKNOWN_REVIEW_RECORD_BEFORE_ANY_RETRY',error=repr(exc));raise
finally:
    r.record['finished_local']=datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8))).isoformat();r.save()
