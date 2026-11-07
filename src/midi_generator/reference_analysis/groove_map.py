"""Read-only mix-audio accent abstraction, never a drum transcription."""
import math
from .audio_loader import load_audio, audio_dependencies


def analyze_audio_groove(path, *, start_seconds, bpm, bar_origin_seconds, bars=4):
    if type(bars) is not int or bars not in (4, 8):
        raise ValueError('Prototype requires 4 or 8 bars under a 4/4 hypothesis')
    for value in (bpm, start_seconds, bar_origin_seconds):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ValueError('Tempo and times must be finite numbers')
    if bpm <= 0 or start_seconds < 0 or bar_origin_seconds < 0:
        raise ValueError('Invalid tempo or time')
    if abs((start_seconds-bar_origin_seconds)/(240/bpm)-round((start_seconds-bar_origin_seconds)/(240/bpm))) > 1e-6:
        raise ValueError('Section must start on the supplied hypothesis bar grid')
    end = start_seconds + bars*240/bpm
    loaded = load_audio(path, start_seconds=start_seconds, end_seconds=end, analysis_sample_rate=22050)
    np, _, _ = audio_dependencies()
    from scipy.signal import stft, find_peaks
    from scipy.ndimage import gaussian_filter1d
    sr, hop, fft = loaded.sample_rate, 110, 1024
    freq, times, z = stft(loaded.samples, sr, nperseg=fft, noverlap=fft-hop)
    times = times + loaded.analysis_range['start_seconds']
    magnitude = np.abs(z)
    observations, params, series = [], {}, {}
    for label, lo, hi in [('low',35,180),('body',180,1800),('high',1800,9000)]:
        band = magnitude[(freq>=lo)&(freq<hi)]
        # Magnitude-weighted positive flux avoids amplifying the near-silent tail.
        flux = gaussian_filter1d(np.maximum(np.diff(band,axis=1,prepend=band[:,:1]),0).sum(axis=0),1)
        energy = gaussian_filter1d(20*np.log10(np.maximum(np.sqrt((band**2).sum(axis=0)),1e-10)),1)
        median, mad = float(np.median(flux)), float(np.median(abs(flux-np.median(flux))))
        threshold = max(1e-8, median+2*mad)
        peaks, properties = find_peaks(flux, height=threshold, prominence=max(1e-8,1.5*mad),distance=round(.075*sr/hop))
        params[label] = dict(peak_height=threshold,prominence=max(1e-8,1.5*mad),band_hz=[lo,hi])
        series[label] = flux
        maximum = max(float(flux.max()),1e-10)
        for p in peaks:
            when=float(times[p]);beat=(when-start_seconds)*bpm/60
            if not 0<=beat<bars*4:continue
            cell=round(beat*4);error=beat-cell/4
            before=max(0,p-round(.04*sr/hop));after=min(len(energy),p+round(.03*sr/hop)+1)
            rise=float(energy[p:after].max()-energy[before:p+1].min())
            observations.append(dict(id=f'{label}_{len(observations):03}',status='observed',band=label,
                time_seconds=when,section_beat=beat,strength=float(flux[p]/maximum),energy_rise_db=rise,
                nearest_sixteenth=cell,grid_residual_beats=error,grid_residual_seconds=error*60/bpm,
                composition_grid_eligible=abs(error)<=.08 and 0<=cell<bars*16,
                inferred_role={'low':'kick_like','body':'body_attack','high':'snare_or_cymbal_like'}[label],
                role_status='inferred',instrument_confidence='low',human_verified=False))
    rows=[]
    for bar in range(bars):
        events=[o for o in observations if o['composition_grid_eligible'] and o['nearest_sixteenth']//16==bar]
        cells=[]
        for step in range(16):
            ev=[o for o in events if o['nearest_sixteenth']%16==step]
            score=sum(o['strength'] for o in ev)/3
            cells.append(dict(step=step,beat=step/4,status='inferred',source='audio_features' if ev else 'no_observation',confidence='medium_for_accent_only' if score>=.3 else 'low',accent_score=score,observation_ids=[o['id'] for o in ev],
                kick_candidate_score=max([o['strength'] for o in ev if o['band']=='low'] or [0]),
                snare_or_cymbal_candidate_score=max([o['strength'] for o in ev if o['band']=='high'] or [0])))
        accents=sorted([c for c in cells if c['accent_score']>=.12],key=lambda c:(-c['accent_score'],c['step']))[:4]
        rows.append(dict(bar_index=bar,cells=cells,role_hypotheses=dict(status='inferred',source='audio_features',instrument_confidence='low',not_verified_drum_notes=True),major_accent_steps=sorted(c['step'] for c in accents),
            kick_candidate_steps=[c['step'] for c in cells if c['kick_candidate_score']>=.4],
            snare_candidate_steps=[c['step'] for c in cells if c['snare_or_cymbal_candidate_score']>=.4],
            mapped_band_attack_count=len(events),rhythm_density_per_beat=len(events)/4,
            rest_candidates=[],fill_candidate={'status':'inferred','value':False,'reason':'Density-based proxy only'},
            phrase_boundary={'status':'inferred','source':'composition','at_end':bar==bars-1}))
    densities=[r['mapped_band_attack_count'] for r in rows];typical=float(np.median(densities))
    for row in rows:
        row['fill_candidate']['value']=row['mapped_band_attack_count']>max(8,typical*1.5)
    sim=[]
    for i in range(bars):
        for j in range(i+1,bars):
            a=np.array([c['accent_score'] for c in rows[i]['cells']]);b=np.array([c['accent_score'] for c in rows[j]['cells']]);den=np.linalg.norm(a)*np.linalg.norm(b)
            sim.append(dict(bars=[i,j],cosine=float(a@b/den) if den>1e-12 else None,status='inferred',meaning='accent-grid resemblance, not confirmed motif identity'))
    # Low-energy regions of the MIX are only rest candidates, not isolated drum stops.
    broadband=np.sqrt(np.mean(abs(z)**2,axis=0));floor=float(np.percentile(broadband,15));mask=broadband<floor
    for bar,row in enumerate(rows):
        ix=np.where((times>=start_seconds+bar*240/bpm)&(times<start_seconds+(bar+1)*240/bpm)&mask)[0]
        runs=np.split(ix,np.where(np.diff(ix)>1)[0]+1) if len(ix) else []
        row['rest_candidates']=[dict(start_seconds=float(times[r[0]]),end_seconds=min(end,float(times[r[-1]]+hop/sr)),status='inferred',meaning='low mix energy; not a verified drum or guitar rest') for r in runs if len(r)*hop/sr>=.08]
    eligible=[o for o in observations if o['composition_grid_eligible']]
    return dict(schema_version=1,kind='audio_composition_groove_map_not_drum_transcription',source=loaded.source,
        section=dict(start_seconds=start_seconds,end_seconds=end,bars=bars,bpm=bpm,bar_origin_seconds=bar_origin_seconds,
                     meter=[4,4],meter_status='hypothesis',bpm_status='caller_supplied',grid_status='caller_supplied'),
        method=dict(fft=fft,hop=hop,sample_rate=sr,bands=params,mapping_tolerance_beats=.08,
                    mapping='Raw times retained. Only composition anchors use nearest sixteenth; audio never shifted.',
                    instrument_warning='Low attacks may include bass/guitar/synth; high attacks may include cymbals/guitar.'),
        observations=observations,bars=rows,bar_similarity=sim,
        summary=dict(observation_count=len(observations),grid_eligible_count=len(eligible),
                     grid_eligible_fraction=len(eligible)/len(observations) if observations else 0,
                     mean_accent_score=float(np.mean([c['accent_score'] for r in rows for c in r['cells']])),
                     half_time={'status':'unknown','reason':'No verified snare isolation; cannot establish half-time from these proxies.'},
                     section_boundary={'status':'inferred','source':'composition_window','not_song_section_label':True}),
        warnings=[*loaded.warnings,'No source separation or complete drum transcription. Instrument roles and fill/rest candidates unverified.'])
