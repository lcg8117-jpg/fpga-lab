"""Read-only display anchor. Never substitutes for a controller origin readback."""


def sampled_display_origin(state, request_id, ack_epoch, reset_count):
    if (type(request_id) is not int or request_id <= 0 or
        type(ack_epoch) is not int or not 0 < ack_epoch <= 0xffffffff or
        state.get('board_resets', 0) != reset_count or
        not state.get('connected') or not state.get('authenticated') or
        not state.get('control_fresh') or state.get('error') or state.get('log_error')):
        return None
    latest = state.get('last_control') or {}
    if (latest.get('firmware_id'), latest.get('schema')) != (0x56354733, 0x46443405):
        return None
    # The accepted PLAY ACK is checked by WordEditor. Bind only its immediate
    # CAPTURE interval, not an arbitrary later WAVE or a prior session's sample.
    first = []
    for row in state.get('word_history', ()):
        delta = (row[0]-ack_epoch) & 0xffffffff
        if not 0 < delta < 0x80000000:
            continue
        if not first and delta > 10:
            return None
        if len(row) < 9 or row[3] != 5 or row[4] != 3 or row[5] or row[6] is not True:
            return None
        if type(row[2]) is not int or not -(2**63) <= row[2] < 2**63:
            return None
        if first and (((row[0]-first[-1][0]) & 0xffffffff) != 5 or
                      ((row[1]-first[-1][1]) & 0xffffffff) != 5):
            return None
        first.append(row)
        if len(first) == 3:
            return dict(basis='first_post_ack_capture_encoder', origin=first[0][2],
                        control_epoch=first[0][0], board_ms=first[0][1],
                        request_id=request_id, ack_epoch=ack_epoch,
                        controller_origin_confirmed=False)
    return None
