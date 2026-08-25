"""
Error-based tracking metrics for desired-vs-actual signal pairs.

Pure numpy/scipy — no Qt — so the maths can be exercised on its own.

The mean of e(t) = actual(t) − desired(t) is close to useless for judging a
tune (positive and negative excursions cancel), so everything here is built on
|e| and e², plus a cross-correlation lag estimate and a step-response
overshoot pass.
"""

import numpy as np

try:
    from scipy import signal as _scipy_signal
except ImportError:                                   # pragma: no cover
    _scipy_signal = None


# Longest delay we are willing to call "lag" rather than "not tracking".
_MAX_LAG_S = 0.5


def wrap180(x):
    """Wrap degrees into (−180, 180] so yaw errors don't read as ~360°."""
    return (np.asarray(x, dtype=np.float64) + 180.0) % 360.0 - 180.0


def unwrap360(x):
    """Remove 360° discontinuities — heading must be continuous before any
    lag or step measurement, otherwise every wrap reads as a huge jump."""
    return np.degrees(np.unwrap(np.radians(np.asarray(x, dtype=np.float64))))


def _percentile(a, q):
    return float(np.percentile(a, q)) if len(a) else float('nan')


def estimate_lag(t, des, act, max_lag_s=_MAX_LAG_S):
    """
    Cross-correlate actual against desired on a uniform grid.

    Returns (lag_seconds, r, pinned) where a positive lag means *actual
    trails desired* by that much, r is the correlation at that lag, and
    `pinned` is True when the best lag sits on the edge of the search window
    — the real delay is then at least that large. Returns (None, None, False)
    when either signal is flat or there is not enough data.
    """
    t = np.asarray(t, dtype=np.float64)
    if len(t) < 16:
        return None, None, False
    dt = float(np.median(np.diff(t)))
    if not np.isfinite(dt) or dt <= 0:
        return None, None, False

    # Resample onto an even grid — log rates jitter, and correlation lags are
    # only meaningful in samples.
    tu = np.arange(t[0], t[-1], dt)
    if len(tu) < 16:
        return None, None, False
    d = np.interp(tu, t, des)
    a = np.interp(tu, t, act)
    d = d - d.mean()
    a = a - a.mean()
    sd, sa = float(d.std()), float(a.std())
    if sd < 1e-9 or sa < 1e-9:
        return None, None, False

    n = len(d)
    max_lag = int(round(max_lag_s / dt))
    max_lag = max(1, min(max_lag, (n - 8) // 2))
    if max_lag < 1:
        return None, None, False

    # Correlate a fixed interior window of `act` against every candidate
    # offset of `des`. Every lag is then scored over exactly the same number
    # of samples, so no lag is favoured by having more (or fewer) terms.
    a_trim = a[max_lag: n - max_lag]
    m = len(a_trim)
    if m < 8:
        return None, None, False
    if _scipy_signal is not None:
        c = _scipy_signal.correlate(d, a_trim, mode='valid')
    else:                                             # pragma: no cover
        c = np.correlate(d, a_trim, mode='valid')
    c = c / (m * sd * sa)
    # c[j] pairs act[max_lag + i] with des[j + i]; actual trails desired by
    # (max_lag − j) samples.
    lags_sel = max_lag - np.arange(len(c))

    best = int(np.argmax(c))
    c_max = float(c[best])

    # A slow, smooth signal correlates almost as well at every lag in the
    # search window — the peak position is then numerical noise, not a delay.
    if c_max - float(c.min()) < 0.005:
        return None, float(np.clip(c_max, -1.0, 1.0)), False

    # Parabolic fit through the peak — the true delay rarely lands exactly on
    # a sample boundary, and log rates are low enough for that to matter.
    delta = 0.0
    if 0 < best < len(c) - 1:
        y0, y1, y2 = c[best - 1], c[best], c[best + 1]
        denom = y0 - 2.0 * y1 + y2
        if abs(denom) > 1e-12:
            delta = float(np.clip(0.5 * (y0 - y2) / denom, -0.5, 0.5))
    # lags run downward in j, so a rightward shift in j is a smaller lag.
    pinned = best in (0, len(c) - 1)
    return (float((lags_sel[best] - delta) * dt),
            float(np.clip(c_max, -1.0, 1.0)), pinned)


def find_overshoot(t, des, act, min_step, settle_s=1.2, ramp_s=0.3):
    """
    Overshoot after fast setpoint changes, in percent of the step size.

    A step is a change in `desired` of at least `min_step` within `ramp_s`
    that then holds roughly still for `settle_s`. For each one, overshoot is
    how far `actual` shoots past the new setpoint relative to the step size.
    Negative values mean it never reached the setpoint.

    Returns (median_overshoot_pct, n_steps); (None, 0) when no clean step is
    found — which is the normal outcome for smooth autonomous flight.
    """
    t = np.asarray(t, dtype=np.float64)
    des = np.asarray(des, dtype=np.float64)
    act = np.asarray(act, dtype=np.float64)
    if len(t) < 32:
        return None, 0
    dt = float(np.median(np.diff(t)))
    if not np.isfinite(dt) or dt <= 0:
        return None, 0

    w_ramp   = max(2, int(round(ramp_s / dt)))
    w_settle = max(4, int(round(settle_s / dt)))
    n = len(t)

    # The peak of a noisy signal sits above the peak of the response itself,
    # which would read as overshoot that isn't there. Smooth over ~30 ms
    # first — far shorter than any real step response.
    w_smooth = int(round(0.03 / dt))
    act_s = act
    if w_smooth >= 3:
        kern = np.ones(w_smooth) / w_smooth
        act_s = np.convolve(act, kern, mode='same')

    results = []
    i = 0
    while i + w_ramp + w_settle < n:
        step = des[i + w_ramp] - des[i]
        if abs(step) < min_step:
            i += 1
            continue
        seg = des[i + w_ramp: i + w_ramp + w_settle]
        # The setpoint must actually settle, otherwise this is a ramp and
        # "overshoot" has no meaning.
        if float(seg.std()) > 0.25 * abs(step):
            i += 1
            continue
        target = float(seg.mean())
        start  = float(act[i])
        travel = target - start
        if abs(travel) < min_step * 0.5:
            i += 1
            continue
        resp = act_s[i: i + w_ramp + w_settle]
        peak = float(resp.max()) if travel > 0 else float(resp.min())
        results.append((peak - target) / travel * 100.0)
        i += w_ramp + w_settle          # one measurement per step
    if not results:
        return None, 0
    return float(np.median(results)), len(results)


def tracking_metrics(t, des, act, angular=False, norm_floor=0.0,
                     normalize=True, max_lag_s=_MAX_LAG_S, dyn=None):
    """
    Error metrics for one desired/actual pair sampled on a common time base.

    `angular` wraps the error to ±180° (attitude angles) and unwraps the
    signals before the lag/overshoot passes. `norm_floor` is the minimum
    |desired| for a sample to count toward the normalized MAE — near zero
    setpoint the ratio blows up and stops meaning anything. `normalize=False`
    skips that metric entirely, for signals like heading where |desired| is an
    absolute bearing and the ratio would be meaningless. `dyn` is an
    optional (t, des, act) triple used for the lag and overshoot passes; pass
    one contiguous stretch there when the main arrays span several flights,
    since both measurements assume an unbroken time base.

    Returns a dict; scalar entries are float('nan') when undefined.
    """
    t   = np.asarray(t, dtype=np.float64)
    des = np.asarray(des, dtype=np.float64)
    act = np.asarray(act, dtype=np.float64)

    good = np.isfinite(t) & np.isfinite(des) & np.isfinite(act)
    t, des, act = t[good], des[good], act[good]

    out = {
        'n': int(len(t)), 'duration': 0.0,
        'mae': float('nan'), 'rmse': float('nan'),
        'p95': float('nan'), 'max': float('nan'),
        'bias': float('nan'),
        'norm_mae': float('nan'), 'norm_frac': 0.0,
        'lag_s': None, 'corr': None, 'lag_pinned': False,
        'overshoot': None, 'n_steps': 0,
    }
    if len(t) < 8:
        return out

    e = act - des
    if angular:
        e = wrap180(e)
    ae = np.abs(e)

    out['duration'] = float(t[-1] - t[0])
    out['mae']  = float(ae.mean())
    out['rmse'] = float(np.sqrt(np.mean(e ** 2)))
    out['p95']  = _percentile(ae, 95)
    out['max']  = float(ae.max())
    out['bias'] = float(e.mean())          # reported only as a trim hint

    # ── Normalized MAE: error as a fraction of how hard the axis was worked ──
    ades = np.abs(des)
    if normalize:
        floor = max(norm_floor, 0.1 * _percentile(ades, 95))
        mask = ades >= floor
        if mask.sum() >= 8:
            denom = float(ades[mask].mean())
            if denom > 1e-9:
                out['norm_mae']  = float(ae[mask].mean() / denom)
                out['norm_frac'] = float(mask.mean())

    d_t, d_des, d_act = dyn if dyn is not None else (t, des, act)
    if angular:
        d_des = unwrap360(d_des)
        d_act = unwrap360(d_act)
    lag_s, corr, pinned = estimate_lag(d_t, d_des, d_act, max_lag_s=max_lag_s)
    out['lag_s'] = lag_s
    out['corr']  = corr
    out['lag_pinned'] = pinned

    # Step size worth calling a step: well above the noise, scaled to how far
    # this axis swings. Measured around the median so an absolute-bearing
    # signal (heading) is treated the same as one commanded around zero.
    spread  = _percentile(np.abs(d_des - np.median(d_des)), 90)
    step_min = max(norm_floor * 2.0, 0.5 * spread)
    if step_min > 0:
        os_pct, n_steps = find_overshoot(d_t, d_des, d_act, step_min)
        out['overshoot'] = os_pct
        out['n_steps']   = n_steps
    return out
