"""Read-only structured analysis of explicitly selected transcribed MIDI parts."""
from collections import Counter, defaultdict
from hashlib import sha256
from itertools import combinations
import json
from pathlib import Path

from midi_generator.modules.guitar.timeline import read_timeline, bar_segments
from midi_generator.modules.guitar.sync import load_sync_config

VERSION = "1.0"
STATUSES = {"observed", "inferred", "user_verified"}


def record(value, status="observed", **provenance):
    if status=="inferred":
        provenance.setdefault("thresholds", {})
        provenance.setdefault("evidence", provenance.get("method", "Algorithmic interpretation"))
    return {"status": status, "value": value, **provenance}


def selected_tracks(indices, count, name, required=False):
    if not isinstance(indices, (list, tuple)) or (required and not indices):
        raise ValueError(f"{name} must explicitly select MIDI track indices (zero-based)")
    if any(type(i) is not int or not 0<=i<count for i in indices) or len(set(indices))!=len(indices):
        raise ValueError(f"Invalid or duplicate {name}")
    return set(indices)


def absolute_messages(midi):
    for ti, track in enumerate(midi.tracks):
        tick = 0
        for message in track:
            if type(message.time) is not int or message.time<0:
                raise ValueError("MIDI requires nonnegative integer delta ticks")
            tick += message.time
            yield ti, tick, message


def section_definitions(definition, bars, markers, end):
    if definition is not None:
        if not isinstance(definition, dict) or set(definition)-{"metadata", "sections"}:
            raise ValueError("Section JSON must contain sections and optional metadata")
        rows=definition.get("sections")
        if not isinstance(rows,list):
            raise ValueError("sections must be an array")
        result=[]
        previous=0
        for row in rows:
            if not isinstance(row,dict) or set(row)-{"label","start_bar","end_bar","status","evidence"}:
                raise ValueError("Invalid section definition fields")
            first,last=row.get("start_bar"),row.get("end_bar")
            if type(first) is not int or type(last) is not int or not 1<=first<=last<=len(bars):
                raise ValueError("Sections require valid 1-based inclusive bar boundaries")
            if first<=previous:
                raise ValueError("Sections must be ordered and non-overlapping")
            if not isinstance(row.get("label"),str) or not row["label"].strip():
                raise ValueError("A section label must be supplied")
            status=row.get("status","user_verified")
            if status not in STATUSES:
                raise ValueError("Unknown section status")
            result.append(record(dict(label=row["label"],start_tick=bars[first-1]["start_tick"],
                 end_tick=bars[last-1]["end_tick"],start_bar=first,end_bar=last),status,
                 source="section_definition",evidence=row.get("evidence","User-supplied section designation")))
            previous=last
        metadata=definition.get("metadata",{})
        if not isinstance(metadata,dict):
            raise ValueError("metadata must be an object")
        for value in metadata.values():
            if (not isinstance(value,dict) or value.get("status") not in STATUSES or
                    "value" not in value or not isinstance(value.get("evidence"),str)):
                raise ValueError("Metadata entries require status, value and evidence")
        return result,metadata
    result=[]
    grouped=defaultdict(set)
    for tick,label in markers:
        if tick<end:
            grouped[tick].add(label)
    ordered=sorted(grouped)
    for i,tick in enumerate(ordered):
        stop=ordered[i+1] if i+1<len(ordered) else end
        result.append(record(dict(labels=sorted(grouped[tick]),start_tick=tick,end_tick=stop),
                             source="MIDI marker text; section meaning not inferred"))
    return result,{}


def rest_intervals(notes, start, end):
    ranges=sorted((max(start,n["start_tick"]),min(end,n["end_tick"]))
                  for n in notes if n["start_tick"]<end and n["end_tick"]>start)
    cursor=start
    rests=[]
    for left,right in ranges:
        if left>cursor:
            rests.append([cursor,left])
        cursor=max(cursor,right)
    if cursor<end:
        rests.append([cursor,end])
    return rests


def motif_candidates(groups,bars,tpb,warnings):
    windows=[]
    complete=[bar for bar in bars if bar["complete"]][:128]
    if sum(bar["complete"] for bar in bars)>128:
        warnings.append("Motif pair comparison limited to the first 128 complete bars.")
    for bar in complete:
        start,end=bar["start_tick"],bar["end_tick"]
        selected=[g for g in groups if start<=g["start_tick"]<end]
        if not selected:
            continue
        rhythm=tuple((g["start_tick"]-start,tuple(n["end_tick"]-n["start_tick"] for n in g["notes"]))
                     for g in selected)
        pitches=tuple(tuple(n["note"] for n in g["notes"]) for g in selected)
        velocity=tuple(tuple(n["velocity"] for n in g["notes"]) for g in selected)
        windows.append((bar,rhythm,pitches,velocity))
    result=[]
    for a,b in combinations(windows,2):
        ba,ra,pa,va=a
        bb,rb,pb,vb=b
        if ba["time_signature"]!=bb["time_signature"] or ba["quarter_beats"]!=bb["quarter_beats"]:
            continue
        rhythm=ra==rb
        pitch=pa==pb
        shape=[len(p) for p in pa]==[len(p) for p in pb]
        shifts=set()
        if shape:
            shifts={y-x for ca,cb in zip(pa,pb) for x,y in zip(ca,cb)}
        kind=None
        if rhythm and pitch and va==vb:
            kind="exact"
        elif rhythm and shape and len(shifts)==1 and shifts!={0}:
            kind="transposed"
        elif rhythm:
            kind="rhythm_match"
        elif pitch:
            kind="pitch_sequence_match"
        else:
            sa={t for t,_ in ra}
            sb={t for t,_ in rb}
            jaccard=len(sa&sb)/len(sa|sb)
            if jaccard>=.8:
                kind="similar_onsets"
        if kind:
            result.append(dict(first_bar=ba["index"],second_bar=bb["index"],kind=kind,
                  rhythm_equal=rhythm,pitch_equal=pitch,
                  transpose_semitones=next(iter(shifts)) if len(shifts)==1 else None))
    return record(result,"inferred",method="Complete single-bar windows; absolute local ticks; exact ordered note shapes, gates and velocities; onset Jaccard",
                  thresholds={"similar_onset_jaccard":.8,"maximum_complete_bars":128},
                  evidence="Pairwise MIDI signatures. All matches are motif candidates, not confirmed identities.")


def analyze_reference_midi(path, *, guitar_tracks, drum_tracks=(), sections=None, sync_config_path=None):
    source=Path(path)
    raw=source.read_bytes()
    try:
        data=read_timeline(source,set())
    except (OSError,ValueError,EOFError,IndexError) as error:
        raise ValueError(f"Could not parse reference MIDI: {error}") from error
    midi=data["midi"]
    if midi.type==2 or midi.ticks_per_beat<=0:
        raise ValueError("Independent tracks/SMPTE timing are unsupported")
    events=list(absolute_messages(midi))
    if data["pairing"]["unmatched_note_offs"] or data["pairing"]["inferred_note_ends"]:
        raise ValueError("MIDI has unmatched Note On/Off events")
    if any(n["start_tick"]>=n["end_tick"] for n in data["notes"]):
        raise ValueError("Nonpositive note duration")
    pending=defaultdict(list)
    for n in data["notes"]:
        pending[(n["channel"],n["note"])].append(n)
    for rows in pending.values():
        rows.sort(key=lambda n:n["start_tick"])
        if any(a["end_tick"]>b["start_tick"] for a,b in zip(rows,rows[1:])):
            raise ValueError("Overlapping same-channel/pitch notes are ambiguous")
    for kind in ("set_tempo","time_signature"):
        at_tick=defaultdict(set)
        for _,tick,msg in events:
            if msg.type==kind:
                value=msg.tempo if kind=="set_tempo" else (msg.numerator,msg.denominator)
                at_tick[tick].add(value)
        if any(len(values)>1 for values in at_tick.values()):
            raise ValueError(f"Conflicting {kind} events")
    gt=selected_tracks(guitar_tracks,len(midi.tracks),"guitar_tracks",required=True)
    dt=selected_tracks(drum_tracks,len(midi.tracks),"drum_tracks")
    if gt&dt:
        raise ValueError("Guitar and drum track selections overlap")
    guitar=[dict(n,duration_ticks=n["end_tick"]-n["start_tick"],
                 duration_beats=(n["end_tick"]-n["start_tick"])/midi.ticks_per_beat)
            for n in data["notes"] if n["track_index"] in gt]
    if not guitar:
        raise ValueError("Selected guitar tracks have no notes")
    drums=[n for n in data["notes"] if n["track_index"] in dt]
    config=load_sync_config(sync_config_path)
    tpb=midi.ticks_per_beat
    bar_data={**data,"context":{**data["context"]},"warnings":list(data["warnings"])}
    unique_meters=[]
    last_meter=(4,4)
    for event in data["context"]["time_signature_events"]:
        meter=(event["numerator"],event["denominator"])
        if meter!=last_meter:
            unique_meters.append(event)
            last_meter=meter
    bar_data["context"]["time_signature_events"]=unique_meters
    bars=bar_segments(bar_data,0)
    if not bars:
        raise ValueError("No positive MIDI duration")
    warnings=list(data["warnings"])
    # Defaults are MIDI semantics rather than recorded metadata.
    tempo=data["context"]["tempo_events"]
    meters=data["context"]["time_signature_events"]
    defaults=[]
    if not any(e["tick"]==0 for e in tempo):
        defaults.append(dict(field="tempo",tick=0,bpm=120,reason="MIDI default"))
    if not any(e["tick"]==0 for e in meters):
        defaults.append(dict(field="meter",tick=0,time_signature=[4,4],reason="MIDI default"))
    by_start=defaultdict(list)
    for n in guitar:
        by_start[n["start_tick"]].append(n)
    groups=[]
    for tick,notes in sorted(by_start.items()):
        notes=sorted(notes,key=lambda n:(n["note"],n["track_index"],n["channel"]))
        bar=next(b for b in bars if b["start_tick"]<=tick<b["end_tick"])
        groups.append(dict(start_tick=tick,bar=bar["index"],beat_in_bar=(tick-bar["start_tick"])/tpb,
                           notes=notes))
    markers=sorted({(tick,msg.text) for _,tick,msg in events if msg.type=="marker"})
    definition=json.loads(Path(sections).read_text()) if isinstance(sections,(str,Path)) else sections
    section_rows,user_metadata=section_definitions(definition,bars,markers,data["end_tick"])
    kick=[n for n in drums if n["note"] in config["kick_notes"]]
    snare=[n for n in drums if n["note"] in config["snare_notes"]]
    tolerance=config["onset_tolerance_beats"]*tpb
    accent_ticks=[g["start_tick"] for g in groups if max(n["velocity"] for n in g["notes"])>=100]
    pattern_candidates=[]
    ticks=set(accent_ticks)
    for pattern in ((4,4),(3,3,2),(3,2,3),(2,3,3),(5,5,6)):
        for tick in accent_ticks:
            boundaries=[tick+sum(pattern[:i])*tpb/4 for i in range(1,len(pattern)+1)]
            if all(t in ticks for t in boundaries):
                pattern_candidates.append(dict(start_tick=tick,sixteenth_intervals=list(pattern),
                      boundary_ticks=[tick]+boundaries))
    pitch_rows=[]
    for g in groups:
        pitches=sorted({n["note"] for n in g["notes"]})
        low=pitches[0]
        intervals=[p-low for p in pitches]
        pitch_rows.append(dict(start_tick=g["start_tick"],lowest_note=low,intervals=intervals,
                               power_chord_shape=intervals in ([0,7],[0,7,12])))
    summaries=[]
    for section in section_rows:
        value=section["value"]
        start,end=value["start_tick"],value["end_tick"]
        selected=[g for g in groups if start<=g["start_tick"]<end]
        summaries.append(record(dict(section=value,groups=len(selected),
             duration_beats=(end-start)/tpb,
             onset_density_per_quarter=len(selected)/((end-start)/tpb),
             mean_gate_beats=(sum(n["end_tick"]-n["start_tick"] for g in selected for n in g["notes"])/
                              sum(len(g["notes"]) for g in selected)/tpb) if selected else None,
             rests=rest_intervals(guitar,start,end))))
    changes=[]
    for a,b in zip(summaries,summaries[1:]):
        av,bv=a["value"],b["value"]
        changes.append(dict(first=av["section"],second=bv["section"],
                    onset_density_change=bv["onset_density_per_quarter"]-av["onset_density_per_quarter"],
                    mean_gate_change=(bv["mean_gate_beats"]-av["mean_gate_beats"])
                    if av["mean_gate_beats"] is not None and bv["mean_gate_beats"] is not None else None))
    return dict(schema_version=VERSION,
        song_metadata=dict(source=record(dict(name=source.name,sha256=sha256(raw).hexdigest())),
             midi=record(dict(ticks_per_beat=tpb,format=midi.type,end_tick=data["end_tick"],
                       duration_seconds=data["timeline"].seconds(data["end_tick"]),tracks=data["tracks"],
                       tempo_events=tempo,time_signature_events=meters,bars=bars,bar_count=len(bars),
                       complete_bar_count=sum(b["complete"] for b in bars))),
             defaults=record(defaults,"inferred",method="Standard MIDI defaults before the first explicit event"),
             user_annotations=user_metadata),
        sections=dict(definitions=section_rows,summaries=summaries,changes=record(changes)),
        rhythm_features=dict(guitar_groups=record(groups),
             onset_intervals=record([dict(first_tick=a["start_tick"],second_tick=b["start_tick"],
                 interval_beats=(b["start_tick"]-a["start_tick"])/tpb) for a,b in zip(groups,groups[1:])]),
             rests=record(rest_intervals(guitar,0,data["end_tick"])),
             velocity_accents=record(accent_ticks,"inferred",method="Maximum simultaneous note velocity",thresholds={"velocity_minimum":100}),
             duration_accents=record([n["start_tick"] for n in guitar if n["end_tick"]-n["start_tick"]>=tpb/2],
                  "inferred",method="Sustained-note accent candidates",thresholds={"gate_quarter_beats_minimum":.5}),
             rest_accents=record([b for a,b in rest_intervals(guitar,0,data["end_tick"]) if b-a>=tpb/2 and b<data["end_tick"]],
                  "inferred",method="Attack following a rest",thresholds={"rest_quarter_beats_minimum":.5}),
             accent_cycles=record(pattern_candidates,"inferred",method="All velocity-accent boundaries, including closing boundary, must exist on absolute ticks; sixteenth intervals",
                  thresholds={"velocity_minimum":100},evidence="Candidate groupings, not a meter change or a listening-quality judgment"),
             drum_relations=record(dict(kick_notes=config["kick_notes"],snare_notes=config["snare_notes"],
                  kick_events=kick,snare_events=snare,
                  guitar_kick_matches=[g["start_tick"] for g in groups if any(abs(g["start_tick"]-n["start_tick"])<=tolerance for n in kick)],
                  guitar_snare_matches=[g["start_tick"] for g in groups if any(abs(g["start_tick"]-n["start_tick"])<=tolerance for n in snare)]),
                  "inferred",method="Explicitly selected drum tracks with existing note mapping",thresholds={"onset_tolerance_beats":config["onset_tolerance_beats"]},
                  evidence="Instrument interpretation depends on supplied track roles and mapping")),
        pitch_features=dict(notes=record(pitch_rows),
             root_motion_candidates=record([dict(first_tick=a["start_tick"],second_tick=b["start_tick"],
                  semitones=b["lowest_note"]-a["lowest_note"]) for a,b in zip(pitch_rows,pitch_rows[1:])],
                  "inferred",method="Consecutive lowest-note candidate differences; harmonic meaning unverified"),
             root_candidates=record([dict(start_tick=r["start_tick"],note=r["lowest_note"]) for r in pitch_rows],
                   "inferred",method="Lowest simultaneous pitch; control notes retained, harmonic roots unverified"),
             power_chord_candidates=record([r["start_tick"] for r in pitch_rows if r["power_chord_shape"]],
                   "inferred",method="Distinct pitches at one exact onset have intervals [0,7] or [0,7,12] above the lowest note")),
        motifs=motif_candidates(groups,bars,tpb,warnings),
        analysis_metadata=dict(method="reference_midi_v1",guitar_tracks=sorted(gt),drum_tracks=sorted(dt),
             warnings=warnings,bar_policy="MIDI tick zero starts bar 1. Meter changes start a new segment; partial bars remain explicit.",
             motif_policy="Pitch and rhythm are compared independently. No section names inferred.",
             control_note_policy="All selected guitar notes retained; sound-library articulation mapping unknown"))


def render_reference_report(result):
    midi=result["song_metadata"]["midi"]["value"]
    groups=result["rhythm_features"]["guitar_groups"]["value"]
    lines=["# Reference MIDI analysis", "",
           "MIDIの観測、推定、ユーザー確認情報をJSONのstatusで区別する。", "",
           f'- 入力 SHA256 {result["song_metadata"]["source"]["value"]["sha256"]}',
           f'- 小節数 {midi["bar_count"]}（完全小節 {midi["complete_bar_count"]}）',
           f'- MIDI長 {midi["end_tick"]} tick、{midi["duration_seconds"]:.6f}秒',
           f'- ギター選択トラック {result["analysis_metadata"]["guitar_tracks"]}（0始まり）',
           f'- ドラム選択トラック {result["analysis_metadata"]["drum_tracks"]}',
           f'- ギター発音グループ {len(groups)}',
           f'- 記録BPMイベント {midi["tempo_events"]}',
           f'- 記録拍子イベント {midi["time_signature_events"]}', "",
           "## セクション", "",
           "|指定ラベルまたはMIDIマーカー|区分|開始tick|終了tick|",
           "|---|---|---:|---:|"]
    for row in result["sections"]["definitions"]:
        value=row["value"]
        label=str(value.get("label",value.get("labels",[]))).replace("|","／").replace("\n"," ")
        lines.append(f'|{label}|{row["status"]}|{value["start_tick"]}|{value["end_tick"]}|')
    if not result["sections"]["definitions"]:
        lines.append("セクション名の指定とMIDIマーカーはない。")
    lines+=["", "## 推定候補", "",
           f'- ベロシティ強調候補 {len(result["rhythm_features"]["velocity_accents"]["value"])}箇所（最大velocity 100以上）',
           f'- パワーコード形候補 {len(result["pitch_features"]["power_chord_candidates"]["value"])}箇所',
           f'- モチーフ候補 {len(result["motifs"]["value"])}組。完全一致・移調・リズム・音程列を区別。']
    cycles=result["rhythm_features"]["accent_cycles"]["value"]
    if cycles:
        lines += ["", "|開始tick|16分単位の強調間隔|", "|---:|---|"]
        lines += [f'|{r["start_tick"]}|{r["sixteenth_intervals"]}|' for r in cycles]
    else:
        lines += ["今回の強打閾値で定型アクセント周期は検出されなかった。"]
    lines+=["", "## 解釈の制約", "",
            "最低音は根音候補として扱う。制御ノート・キースイッチを推測で除外しない。"
            "拍頭外の打点と実際の拍子を区別する。周期候補の存在から聴感上の問題を判定しない。"
            "JSONに全発音・音価・休符区間・セクション差分と解析方法を保存する。"]
    lines+=["", "## 警告", ""]+[f'- {w}' for w in result["analysis_metadata"]["warnings"]]
    return "\n".join(lines)+"\n"


def save_reference_midi_analysis(result, output):
    """Save a new report pair and roll back partial writes."""
    out=Path(output)
    out.parent.mkdir(parents=True,exist_ok=True)
    serialized=json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False)+"\n"
    markdown=render_reference_report(result)
    out.mkdir(exist_ok=False)
    created=[]
    try:
        for name,text in (("analysis.json",serialized),("report.md",markdown)):
            path=out/name
            with path.open("x",encoding="utf-8") as stream:
                created.append(path)
                stream.write(text)
    except Exception:
        for path in created:
            path.unlink(missing_ok=True)
        out.rmdir()
        raise
    return [out/"analysis.json",out/"report.md"]
