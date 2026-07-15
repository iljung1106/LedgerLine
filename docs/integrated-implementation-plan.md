# LedgerLine 0.6.0 통합 구현 계획 — 완료·보관

상태: 역사적 구현 기준안. 실제 릴리스 후보 상태와 검증 결과는
[`development-status.md`](development-status.md) 및
[`release-verification-0.6.0.md`](release-verification-0.6.0.md)를 따른다.

작성일: 2026-07-14 · 구현 검토일: 2026-07-14

대상: LedgerLine Core, LedgerLine Studio, Codex plugin

이 문서의 미래형 문장과 alpha/beta 단계명은 구현 당시의 의사결정과 exit criteria를 보존하기
위한 것이다. 현재 코드 구조는 문서 말미의 **실제 구현 매핑**을 기준으로 한다.

## 1. 결론

LedgerLine의 다음 목표는 범용 DAW를 새로 만드는 것이 아니다. 목표는 다음의 한 문장으로
고정한다.

> Codex가 간단한 음악 초안을 빠르게 작성하고, 형식·동기·화성·보이싱·오케스트레이션·
> 연주 표현·음향을 단계적으로 전문화한 뒤, 실제 LedgerLine 엔진이 만든 결과를 사용자가
> Studio에서 듣고 보고 수정하며 다시 Codex에게 넘길 수 있게 한다.

이를 위해 세 가지를 동시에 완성한다.

1. **음악적 refinement loop**: 초안을 구체적이고 전문적인 곡으로 발전시키는 명시적 단계와
   분석 자료, 편집 명령, 청취 checkpoint를 제공한다.
2. **engine-truth Studio**: 악보, MIDI, stem, mix, 파형, 분석, 악기 상태가 모두 동일한 project
   revision을 가리키며, 오픈소스 작곡 도구의 핵심 수준으로 편집할 수 있게 한다.
3. **Codex plugin loop**: 방향 확인부터 초안, refinement, 렌더, Studio 검토, 재수정, 최종 전달을
   하나의 플러그인 작업 흐름으로 연결한다.

완료 판정은 명령 성공이나 자동 품질 점수가 아니다. 구조적·기술적 검사는 도구가 담당하고,
음악적 선택은 Codex가 근거를 설명하며, 중요한 checkpoint의 최종 판단은 사용자의 청취로
남긴다.

## 2. 제품 원칙

### 2.1 유지할 원칙

- 에이전트가 음표, 리듬, 화성, 악기 역할, 표현, 믹스 결정을 명시적으로 작성한다.
- 도구는 근거를 계산하고 오류를 검출하지만 취향을 점수 하나로 환원하지 않는다.
- 선택한 악기, preset, articulation이 없으면 조용히 대체하지 않는다.
- 화면에 보이는 악보와 들리는 오디오는 반드시 revision과 freshness 상태를 드러낸다.
- 전문 용어를 모르는 사용자는 결과와 느낌만 말해도 되며, 기술적 세부 선택은 에이전트가
  맡아서 설명 가능한 결정으로 변환한다.
- 모든 Studio 편집은 Codex와 CLI에서도 동일한 명령 계약으로 읽고 재현할 수 있어야 한다.

### 2.2 이번 계획의 비목표

다음은 핵심 경로를 완성한 뒤에만 검토한다.

- 다중 사용자 실시간 공동 편집, CRDT, 계정·권한 서버
- 분산 render farm 또는 범용 worker orchestration
- 브라우저에서 오프라인 렌더러와 동일한 DSP 전체 재구현
- 자체 범용 VST3/CLAP 호스트를 처음부터 개발하는 일
- 플러그인 자체 GUI의 Studio 내 임베딩
- 전문 출판용 악보 engraving과 모든 MusicXML 요소의 무손실 왕복
- 바이너리 MIDI 2.0 UMP 실시간 전송
- 악기 marketplace, 결제, 클라우드 asset 동기화
- 자동 미학 점수 또는 사용자의 청취를 대체하는 최종 품질 판정

## 3. 목표 사용자 흐름

### 3.1 새 곡

```text
사용자 방향 제시
  → Codex가 brief와 보존 조건 확정
  → 8~16마디 대표 초안 작성
  → 실제 악기로 sketch render
  → 사용자 방향 확인
  → 전체 구조 draft 작성
  → 전문 refinement passes
  → production render와 mix
  → Studio 자동 실행
  → 직접 편집 또는 자연어 수정 요청
  → 변경된 범위만 재검증·재렌더
  → A/B 청취와 승인
  → lock·bundle·delivery
```

### 3.2 기존 곡 개선

```text
프로젝트 열기
  → revision과 engine 상태 검사
  → 악보·MIDI·stem freshness 확인
  → 사용자가 보존할 요소 지정
  → refinement 진단 생성
  → Codex가 범위가 제한된 수정안 제시
  → Studio diff와 A/B 검토
  → 승인된 수정만 적용·재렌더
```

### 3.3 Studio에서 자연어 위임

```text
사용자 요청 기록
  → 현재 Codex가 요청 읽기
  → 필요 시 needs-direction 질문
  → 수정 계획과 영향 범위 제시
  → review 또는 제한된 safe-auto 적용
  → validate·render·mix
  → 제안 전후 A/B와 listening check 표시
```

Studio 버튼이 독립적으로 새 Codex 작업을 생성하는 기능은 이번 핵심 범위에 포함하지 않는다.
플러그인을 실행 중인 Codex가 요청을 처리하는 단순한 파일/API 계약을 먼저 완성한다.

## 4. 음악적 refinement 모델

### 4.0 현재 구현에서 이어갈 것과 채울 간격

현재 `inspect`는 온셋의 기본 chord name과 파트별 음역·평균 density를 제공하고, motif는 이미
transpose, invert, retrograde, augment, diminish, rhythm 변형을 지원한다. 이 기능은 폐기하지
않고 refinement report의 기초로 재사용한다.

현재 부족한 것은 분석 결과가 구조·성부·오케스트레이션·표현의 구체적인 finding과 편집
proposal로 이어지는 연결이다. Studio command 역시 기존 음표의 수정·이동·길이, 범위
transpose/velocity/articulation, tempo와 일부 mix 편집에 집중되어 있어 새 phrase 작성이나
전문적인 control/automation 수정에는 부족하다. 따라서 별도의 자동 작곡기를 만들기보다
분석 finding, 명시적 edit command, 실제 렌더 A/B를 연결한다.

### 4.1 두 단계 작곡 전략

에이전트에게 처음부터 모든 디테일을 동시에 요구하지 않는다.

**Draft mode**는 빠르게 음악의 정체성을 검증한다.

- 한두 개의 핵심 motif
- section과 길이
- 기본 화성 진행과 bass motion
- 주선율, 핵심 counterline, pulse
- 대략적인 악기 역할과 register
- 기본 dynamics와 임시 mix

**Refinement mode**는 보존 조건을 잠근 뒤 한 축씩 전문화한다.

- 사용자가 승인한 motif, melody, form, instrumentation은 invariant로 기록한다.
- 한 pass에서 바꾸는 주된 축을 제한한다.
- 각 pass는 진단 근거, 변경 범위, 예상 효과, listening check를 남긴다.
- 구조를 바꾸는 수정과 음색·믹스 수정을 한 proposal에 섞지 않는 것을 기본으로 한다.

### 4.2 여섯 개 refinement pass

| Pass | 확인할 것 | 대표 수정 | 통과 조건 |
|---|---|---|---|
| 1. Identity & form | motif 식별성, phrase 방향, section 대비, transition, ending | motif 변형, phrase 길이, cadence 강도, section 밀도 | 각 section의 역할과 도착점이 설명되고 반복이 무의미하지 않음 |
| 2. Harmony & voice leading | chord/bass 관계, harmonic rhythm, tendency tone, 공통음, voice crossing | inversion, inner voice, suspension, bass line, revoicing | 범위 내에서 의도하지 않은 충돌이 해소되고 화성 긴장이 form을 지지함 |
| 3. Arrangement & orchestration | foreground/counterline/support/bass/pulse 역할, 음역, blend, playability | 역할 재배치, doubling, divisi, register, rest, articulation | 모든 활성 파트의 역할이 있고 중요한 선율이 masking되지 않음 |
| 4. Performance & expression | phrase arc, attack와 sustain, breath/bow/pedal, tempo rhetoric | velocity, CC1/11, pedal, tempo curve, articulation/keyswitch | 선택 악기 capability와 일치하며 표현 데이터가 phrase를 따라감 |
| 5. Production | gain staging, depth, spectral conflict, transient, loudness | gain/pan/send, EQ, compressor, reverb, automation | clipping 없이 목표 loudness 범위에 있고 주요 line이 의도한 depth에 위치함 |
| 6. Listening revision | theme, pacing, emotion, realism, translation | 사용자가 승인한 범위의 수정 | matched-loudness A/B와 review 기록이 남고 미해결 항목이 명시됨 |

### 4.3 생성할 분석 근거

분석기는 결정을 대신하지 않고 Codex와 사용자에게 다음 근거를 제공한다.

#### Structure report

- section별 길이, density, register, dynamic, harmonic rhythm
- motif 출현 위치와 transpose/invert/rhythm 변형 관계
- phrase 후보와 cadence 후보
- transition 전후 변화량
- 끝맺음 직전의 tempo·density·harmony 변화

#### Harmony report

- 선택 가능한 chord label과 bass note; 모호성은 복수 후보로 유지
- scale-degree 또는 pitch-class 관점의 non-chord tone
- voice crossing, spacing, parallel interval 후보
- tendency tone resolution, doubled leading-tone 후보
- low-register interval과 지속적인 dissonance 후보
- section별 harmonic rhythm과 pedal point

#### Orchestration report

- 악기별 absolute/comfortable range 사용량
- 시간 구간별 역할, 활성 파트 수, register occupancy
- 같은 register에서 경쟁하는 line 후보
- breath/bow/hand-span 및 recovery-time 경고
- articulation/keyswitch capability와 실제 사용의 일치
- 의도 없는 장시간 doubling 또는 full-ensemble density 후보

#### Expression report

- phrase별 velocity·CC1·CC11 곡선과 dynamic 표기의 관계
- sustain pedal과 harmony change의 관계
- articulation 반복, keyswitch timing, expression overlap 오류
- tempo ramp와 cadence/transition의 정렬
- 지나치게 균일한 attack·sustain 값의 구간

#### Production report

- stem과 bus의 peak, RMS, crest, LUFS, true peak
- 시간 구간별 주요 stem의 상대 level
- spectral centroid와 저역 중첩 후보
- reverb send와 depth 분포
- silence, clipping, 과도한 limiter reduction 후보

위 보고서는 `build/refinement/` 아래의 생성물이며 authored source가 아니다.

모든 pass는 같은 finding envelope를 사용한다.

```json
{
  "id": "orchestration.register-collision.12",
  "domain": "orchestration",
  "severity": "warning",
  "scope": {
    "from": "12:1",
    "to": "14:end",
    "parts": ["cello", "piano"]
  },
  "evidence": {
    "register": "C2-G2",
    "overlap_seconds": 5.2
  },
  "message": "두 저역 역할이 같은 음역에서 지속됩니다.",
  "suggestions": [
    "피아노 왼손을 한 옥타브 올림",
    "첼로만 bass 역할로 유지"
  ]
}
```

`suggestions`는 에이전트가 검토할 선택지이며 engine이 자동 적용하는 명령이 아니다.

### 4.4 자동화하지 않을 음악적 판단

- motif가 기억에 남는가
- 화성이 감동적인가
- groove가 좋은가
- orchestration이 세련되었는가
- 사용자가 원한 정서에 도달했는가

도구는 이 질문에 필요한 비교 자료를 제공한다. Codex는 선택 이유를 설명하고, 사용자는
checkpoint에서 듣고 승인하거나 방향을 바꾼다.

### 4.5 품질 gate

**Hard gate**는 source 또는 실제 재생 결과가 명백히 잘못되는 경우다.

- schema, meter와 voice duration, tie, staff 유효성
- 악기의 absolute range와 transposition
- 선택 profile에 없는 articulation, keyswitch, semantic control
- expression transport overlap과 channel/capability 충돌
- renderer/instrument/preset 존재와 정확한 binding
- 최신 project revision과 stem/master receipt의 일치
- render 실패와 clipping/true-peak ceiling 위반

**Review gate**는 음악적으로 살펴볼 근거이며 자동 실패로 만들지 않는다.

- motif의 부족 또는 과도한 반복
- phrase climax와 cadence 강도
- style에 따라 달라지는 parallel fifth/octave
- unresolved tension과 non-chord tone
- doubling, density, register competition, masking 후보
- 지나치게 평평한 expression과 tempo

Review finding은 사용자가 이유를 남기고 waive할 수 있다. 실험음악이나 특정 시대의 문법을
일반 규칙으로 막지 않는다.

## 5. 데이터와 편집 계약

### 5.1 `brief.yaml`

`NOTES.md`는 자유 기록으로 유지하되, 플러그인과 Studio가 읽을 최소 creative brief를
`brief.yaml`로 추가한다.

```yaml
format: 1
purpose: game scene
duration_seconds: 150
trajectory: [intimate, uncertain, resolute]
references:
  - "slow chamber nocturne; transparent texture"
required_instruments: [piano, cello]
forbidden_sounds: [large cinematic percussion]
sections:
  - {id: A, from: "1:1", to: "8:end", function: exposition}
roles:
  - {part: cello, from: "1:1", to: "8:end", role: foreground}
protected:
  - {from: "1:1", to: "4:end", parts: [cello], aspects: [pitch, rhythm]}
invariants: ["do not increase the ensemble beyond four players"]
style_checks: {parallel_fifths: review, low_register_spacing: review}
checkpoints: [representative-sketch, structural-draft, production-render, final]
```

기계가 읽는 항목은 creative direction, section 경계, 역할, 보호 범위, 선택적 style check로
제한한다. 상세한 감정 설명과 결정의 역사는 `NOTES.md`에 유지하며 별도의 복잡한 ontology는
만들지 않는다.

### 5.2 영속 event ID

Studio와 Codex가 같은 음표를 지속적으로 참조하려면 위치 기반 ID를 대체해야 한다.

- 기존 format 1 schema에 optional `id`를 추가하고, 새 프로젝트와 Studio 편집 대상 event에는
  명시적 ID를 요구한다. ID만을 위해 전체 authored format을 올리지 않는다.
- control event와 automation point에도 ID를 둔다.
- chord의 개별 pitch는 event ID와 pitch index로 참조한다.
- 기존 프로젝트는 계속 compile할 수 있고 `ledgerline prepare-ids`가 snapshot과 dry-run diff 후
  명시적 ID를 기록한다.
- Studio는 ID가 없는 프로젝트를 자동 덮어쓰지 않고 준비 필요 상태를 보여준다.

### 5.3 공통 edit command

UI와 Codex가 같은 명령을 사용한다.

- event: `insert`, `delete`, `update`, `move`, `resize`, `duplicate`
- voice: `replace_measure_voice`로 에이전트가 한 성부를 명시적 event 목록으로 다시 쓸 수 있다.
- selection: `transpose`, `quantize`, `scale_velocity`, `set_articulation`
- controls: `insert_control`, `update_control`, `delete_control`
- automation: `insert_point`, `move_point`, `delete_point`, `set_curve`
- form: `copy_range`, `replace_range`는 review 모드에서만 허용
- mix: track/bus/master gain·pan·routing·send·processor parameter
- instrument: profile/preset 변경은 capability 재검사와 render invalidation을 동반

모든 command는 예상 project revision, 영향받는 ID와 범위, undo payload를 가진다.

### 5.4 저장과 undo

전체 event-sourcing은 만들지 않는다.

- YAML은 같은 디렉터리의 임시 파일에 쓴 뒤 atomic replace한다.
- `.ledgerline/history/`에 최근 N개 transaction과 변경 전 파일 snapshot을 저장한다.
- 서버 재시작 뒤에도 제한된 undo/redo를 복구한다.
- transaction 적용 후 schema, project, compile validation을 통과하지 못하면 원복한다.
- 여러 Studio 탭의 충돌은 expected revision mismatch로 거부한다.

## 6. engine truth와 build freshness

### 6.1 단일 상태 문서

`build/state.json`을 Studio와 Codex가 공유하는 생성 상태의 기준으로 추가한다.

```json
{
  "project_revision": "...",
  "stages": {
    "compile": {"status": "ready", "input_revision": "..."},
    "render:piano": {"status": "stale", "input_revision": "..."},
    "render:cello": {"status": "ready", "input_revision": "..."},
    "mix": {"status": "blocked", "reason": "piano stem is stale"},
    "analysis": {"status": "stale"}
  }
}
```

상태는 최소한 `authored_revision`, `compiled_revision`, part별 `render_key`,
`mix_input_hash`를 서로 구분한다. Codex가 열린 Studio 밖에서 YAML을 수정해도 새 YAML과 이전
MusicXML/WAV를 한 revision처럼 합치지 않는다. 복잡한 filesystem watcher는 두지 않고 Studio의
짧은 `/api/status` polling과 플러그인의 명시적 rebuild 호출로 동기화한다.

실제 문서에는 다음 hash를 연결한다.

- authored score, controls, automation, mix, performance, render config
- instrument profile와 selected preset/state
- renderer executable와 version
- MIDI/expression plan
- output stem과 master

새 cache 체계를 병렬로 만들지 않는다. 기존 render graph의 part MIDI, instrument, state,
automation 기반 cache key와 render receipt를 compile/mix manifest에 연결하고, `build/state.json`은
그 결과를 Studio가 읽기 쉽게 요약하는 projection으로 둔다.

render receipt와 Studio state에는 configured 값이 아니라 실제 렌더에 사용된 provenance를
기록한다.

- engine과 executable path/version/hash
- SoundFont/SFZ/plugin hash와 bank/program/preset/state
- instrument profile, articulation/keyswitch capability
- cache hit/miss, latency/tail, output hash
- 실패·quarantine 원인

파일 길이만으로 freshness를 판정하지 않는다.

### 6.2 media sidecar와 versioned URL

Studio model polling 때 WAV 전체를 다시 읽어 peak를 계산하지 않는다.

- render/mix 완료 시 output SHA별 peak JSON과 spectrogram sidecar를 한 번 생성한다.
- media URL에는 `?v=<output-sha>`를 붙여 browser cache와 revision을 일치시킨다.
- 같은 SHA의 분석 sidecar가 있으면 재사용한다.
- 이전 revision의 media는 A/B 또는 명시적 과거 결과로만 표시한다.

### 6.3 invalidation 규칙

- 한 part의 note/control/profile 변경 → 해당 part compile/render, mix, analysis stale
- tempo 변경 → 모든 time-bound compile/render/mix/analysis stale
- track gain/pan/send/effect 변경 → stem은 유지, mix와 analysis stale
- master 변경 → master mix와 analysis만 stale
- review note 변경 → audio artifact는 유지
- brief 변경 → artifact는 유지하되 refinement report를 stale로 표시

### 6.4 단순 로컬 job runner

상태는 다음 다섯 개면 충분하다.

```text
queued → running → ready
                 ↘ failed
queued/running → cancelled
```

필수 기능은 FIFO, subprocess timeout, progress event, 취소, log, 실패 원인, 변경된 part만 다시
실행하는 것이다. 분산 worker나 외부 message broker는 사용하지 않는다.

별도 증분 renderer를 만들지 않는다. score 변경은 기존 render graph 전체를 호출하되 node hash
cache가 변경된 part만 실제 렌더하게 한다. mix-only 변경은 render stage를 건너뛴다. 빠르게
이어지는 편집은 아직 시작하지 않은 중간 job을 coalesce한다.

Studio API는 다음 작업을 요청할 수 있다.

- validate
- compile
- render changed parts
- mix
- analyze/refinement-report
- prepare review bundle

긴 작업은 HTTP 요청 thread에서 직접 실행하지 않는다. Studio는 Server-Sent Events 또는 짧은
polling으로 진행률을 받는다. 초기 구현은 polling으로 시작하고 필요할 때 SSE로 바꾼다.

## 7. LedgerLine Studio 요구사항

Studio는 오픈소스 DAW/notation tool 전체를 복제하지 않는다. 작곡 결과를 검토하고 의미 있게
수정하기 위한 공통 핵심을 제공한다.

### 7.1 공통 transport와 timeline

- play, pause, stop, seek, loop range
- measure/beat ruler, tempo와 meter marker
- zoom, horizontal scroll, fit selection/project
- 모든 lane과 score가 같은 playhead를 사용
- 선택 구간, review marker, stale 범위 표시
- 실제 render tail과 musical duration을 구분

### 7.2 piano roll

- note/chord 생성·삭제·이동·길이 조정·복제
- box/multi selection, shift add/remove, copy/paste
- grid와 snap resolution, quantize strength
- part/voice/staff 필터
- register를 벗어난 음과 capability 오류 표시
- chord event를 유지한 채 개별 pitch 또는 전체 chord 편집
- keyboard shortcut과 undo/redo

이번 범위에서 제외하는 것: score 전체를 자동 reharmonize하는 UI, 복잡한 pattern sequencer,
비선형 clip launcher.

### 7.3 performance lanes

- velocity
- CC1 modulation
- CC11 expression
- CC64 pedal을 semantic down/change/up으로 표시
- pitch bend 또는 cents curve
- tempo와 tempo ramp
- articulation/keyswitch event
- automation point의 선택·추가·이동·삭제와 curve type

선택 instrument profile이 지원하지 않는 lane이나 articulation은 비활성화하고 이유를 표시한다.

### 7.4 score

- MusicXML score와 정확한 playback cursor
- 악보 클릭 seek
- score note 선택과 piano roll/inspector 선택 연동
- edit 이후 compile 완료 시 score 자동 reload
- multi-staff/voice highlight

직접 notation engraving 편집은 후순위다. 핵심 note edit는 piano roll과 inspector가 담당한다.

### 7.5 audio review

- part별 실제 rendered stem 재생
- master와 stem 파형
- master spectrogram 또는 spectrum
- loop/scrub와 review marker
- stale audio를 재생할 때 명확한 banner
- revision이 다른 score와 audio를 A/B용이 아닌 한 함께 최신처럼 표시하지 않음

브라우저 preview는 stem의 gain/pan/mute/solo만 즉시 반영한다. bus/effect/master의 정확한 결과는
LedgerLine 오프라인 mix를 다시 만들어 재생한다.

stem이 없을 때의 oscillator fallback은 `MIDI sketch audition`으로 분리해 표시하고 production
검토 mode에서는 기본으로 끈다. 사용자가 이를 실제 선택 악기의 render로 오해하게 해서는 안 된다.

### 7.6 mixer

- track/bus/master strip
- 실제 peak/RMS meter; master LUFS/true peak
- gain, pan, mute, solo
- output routing과 send level
- 기존 EQ/compressor/reverb processor의 parameter 편집
- automation 존재·read 상태 표시
- save 시 mix stale, rebuild 시 실제 master 갱신

가짜 level meter 또는 gain 값을 level처럼 표시하는 UI는 제거한다.

### 7.7 instrument와 engine panel

- part별 renderer, instrument path, profile, bank/program 또는 preset/state
- range, articulation, keyswitch, semantic parameter capability
- executable/version/hash와 render receipt
- missing/blocked/quarantined 상태와 정확한 복구 방법
- profile이 허용하는 preset 변경과 짧은 audition

임의 VST GUI embed는 하지 않는다. 지원되는 외부 host adapter가 제공한 scan/state contract만
표시하고 편집한다.

### 7.8 Codex panel

- plain-language 요청
- 보존 조건과 변경 허용 범위
- pending, needs-direction, proposed, applying, rendering, ready, failed 상태
- Codex 질문과 사용자 답변
- proposal의 음악적 이유, 영향받는 section/part/event 수
- 적용 전 source diff와 적용 후 A/B
- listening check와 승인·거절·추가 수정

`safe-auto`는 명령 종류뿐 아니라 변경 예산을 통과해야 한다.

모든 task와 proposal은 `base_revision`을 가진다. 현재 revision이 달라졌다면 적용을 거부하고
proposal을 다시 만들도록 한다.

- 최대 measure/event 수
- transpose·tempo·gain 변화 한도
- invariant와 locked range 보존
- instrument/profile 변경 금지
- form 변경 금지
- 예산 초과 시 자동으로 review 모드 전환

## 8. Codex plugin 동작 계약

### 8.1 시작

플러그인은 다음 순서를 수행한다.

1. `bootstrap -Plan`과 `doctor --json`으로 환경과 실제 악기를 확인한다.
2. 필요한 download/setup은 기존 consent 절차를 따른다.
3. `brief.yaml`을 작성하거나 기존 brief를 확인한다.
4. representative sketch를 작성하고 실제 선택 악기로 render한다.
5. checkpoint에서 방향을 확인한 뒤 structural draft를 진행한다.
6. refinement report를 읽고 여섯 pass를 범위가 제한된 transaction으로 수행한다.
7. 각 중요한 pass 뒤 validate하고, 필요할 때만 변경 part를 render한다.
8. production render 후 Studio를 시작하고 URL과 현재 revision을 사용자에게 제공한다.

### 8.2 Studio handoff

`ledgerline studio <project> --prepare`를 추가한다.

- 현재 revision을 검사한다.
- compile이 stale이면 compile job을 실행한다.
- render/mix가 없거나 stale이면 상태를 보여주고, 플러그인에서 승인된 renderer로 준비한다.
- Studio server를 시작한다.
- machine-readable JSON으로 URL, token, project revision, engine state를 반환한다.

플러그인은 Studio가 열린 동안 delegation 요청을 확인한다. 지속적인 background daemon 대신
Codex의 현재 작업 loop에서 checkpoint마다 확인한다.

### 8.3 제안 형식

Codex proposal에는 다음을 요구한다.

- 사용자 목표의 해석
- 보존할 invariant
- 선택한 refinement pass
- 변경할 part/measure/event 범위
- edit commands
- render invalidation 예상
- 기대하는 audible effect
- listening check
- 질문 또는 불확실성

```json
{
  "pass": "orchestration",
  "summary": "첼로와 피아노의 저역 역할을 분리합니다.",
  "scope": {"from": "9:1", "to": "16:end"},
  "preserve": ["cello melody", "harmony"],
  "evidence_ids": ["orchestration.register-collision.12"],
  "actions": [],
  "expected_effect": "저역 선명도와 첼로 선율의 가독성 향상",
  "listening_check": ["bass clarity", "cello presence"],
  "requires_review": true
}
```

검증 성공만으로 `musically improved`라고 보고하지 않는다.

## 9. 구현 단계

각 단계는 독립적으로 사용 가능한 결과와 exit criteria를 가진다. P0가 끝나기 전에 장식적인 UI를
늘리지 않는다.

| Historical milestone | 포함 단계 | 의미 |
|---|---|---|
| `0.6.0-alpha.1` | Phase 0~1 | ID, freshness, local jobs, plugin handoff 기반 |
| `0.6.0-alpha.2` | Phase 2 | 실제 검토에 쓸 수 있는 Studio 편집 핵심 |
| `0.6.0-beta.1` | Phase 3~4 | 음악 refinement와 실제 mixer/instrument 상태 |
| `0.6.0-beta.2` | Phase 5 | Codex 요청·제안·재렌더 loop |
| `0.6.0` | Phase 6 | Windows 검증과 packaged plugin 안정 릴리스 |

### Phase 0 — 계약과 ID 준비 기반 (P0)

구현:

- `brief.schema.json`
- 기존 format 1의 backward-compatible optional ID와 Studio 편집 시 ID requirement
- `studio-command.schema.json`, `studio-state.schema.json`
- `prepare-ids`의 non-destructive snapshot, dry-run과 backup
- 현재 0.5 기능표와 문서의 버전 정리

Exit criteria:

- 기존 example과 format 1 프로젝트가 그대로 compile된다.
- ID 준비 결과의 MusicXML/MIDI 의미가 준비 전과 동일하다.
- Studio와 CLI가 같은 event ID를 사용한다.
- schema fixture와 backward-compatibility test가 통과한다.

### Phase 1 — engine truth와 Codex handoff (P0)

구현:

- `build/state.json`과 hash-based freshness
- SHA-keyed peak/spectrogram sidecar와 versioned media URL
- invalidation planner
- 단순 local job runner
- atomic Studio transaction과 disk history
- `studio --prepare`, job/status/cancel API
- Windows에서 프로젝트별 단일 Studio process의 start/status/stop lifecycle
- plugin skill의 draft → render → Studio handoff 갱신

Exit criteria:

- 음표 하나를 변경하면 해당 stem만 stale이고 mix/analysis가 stale이 된다.
- render 후 stem receipt와 project revision이 일치해야 ready가 된다.
- 같은 길이의 오래된 WAV를 aligned로 오판하지 않는다.
- 같은 output SHA의 peak/spectrogram은 polling 때 다시 계산하지 않는다.
- 프로세스를 재시작해도 최근 transaction을 undo할 수 있다.
- Codex plugin에서 한 명령 흐름으로 Studio URL을 받는다.
- Codex가 Studio 밖에서 source를 바꾸면 UI가 revision 차이를 감지해 이전 score/audio를 stale로
  표시하고, rebuild 뒤 같은 URL에서 갱신한다.
- Studio의 engine, executable, asset, preset/state 표시는 render receipt와 정확히 일치한다.

### Phase 2 — 편집 가능한 Studio core (P0)

구현:

- piano roll CRUD, resize, multi-select, copy/paste, snap/quantize
- velocity와 CC1/11/64, articulation, tempo lanes
- score click seek와 note selection 연동
- shared transport, loop, zoom, selection
- 실제 stem waveform과 stale state
- edit command validation 및 optimistic revision check

Exit criteria:

- UI만 사용해 8마디 phrase를 새로 입력하고 수정할 수 있다.
- note, control, tempo 편집이 YAML, MusicXML, MIDI에 일관되게 반영된다.
- score, piano roll, waveform이 같은 playhead와 selection을 사용한다.
- undo/redo 후에도 event ID와 selection이 유지된다.

### Phase 3 — 전문 refinement toolkit (P1)

구현:

- structure, harmony, orchestration, expression report
- optional `ledgerline refine inspect --passes ... --json`
- invariant/locked-range 검사
- pass별 proposal template과 change budget
- section·part·measure를 기준으로 한 before/after diff
- 최소 전문 표기·표현 확장: tuplet, grace note, slur, hairpin/dynamic ramp, tempo ramp,
  profile-defined articulation vocabulary
- plugin musical-quality 지침을 pass 기반 workflow로 개정

Exit criteria:

- 예제 초안에 대해 여섯 pass 중 production 전 네 pass의 근거 보고서를 생성한다.
- Codex가 각 finding을 event/measure/part에 연결할 수 있다.
- invariant를 위반하는 proposal은 apply 전에 실패한다.
- 같은 초안을 refinement한 예제에서 변경 이유와 listening check가 모두 기록된다.
- 보고서는 미학 점수를 생성하거나 무단으로 source를 수정하지 않는다.

### Phase 4 — 실제 mixer와 instrument 상태 (P1)

구현:

- track/bus/master mixer UI
- Web Audio 기반 실제 peak/RMS preview meter
- master LUFS/true-peak 결과 표시
- send와 EQ/compressor/reverb parameter editing
- engine/instrument/profile/preset/capability panel
- changed-only render와 mix rebuild controls
- master/stem A/B와 matched loudness 비교

Exit criteria:

- UI의 모든 authored mix 값이 `mix.yaml`과 일치한다.
- meter는 실제 재생 신호에서 계산되며 장식 값이 아니다.
- offline mix 후 표시되는 LUFS/true peak가 CLI measurement와 일치한다.
- instrument 변경 시 capability를 재검사하고 해당 part만 render stale이 된다.

### Phase 5 — Codex refinement loop 완성 (P1)

구현:

- needs-direction 질문/답변 상태
- proposal impact summary와 source diff
- review/change-budget safe-auto
- apply → validate → render → mix → A/B의 orchestration
- Studio checkpoint와 plugin polling 계약
- 실패 recovery와 quarantine 안내

Exit criteria:

- 비전문 사용자가 “후반부를 더 절박하게, 첼로 선율은 보존”이라고 요청할 수 있다.
- Codex가 필요하면 한 번 질문하고, pass와 범위를 정해 수정안을 만든다.
- 사용자는 Studio에서 악보 diff와 A/B를 확인한 뒤 승인·거절할 수 있다.
- 승인 시 변경된 part만 재렌더되고 같은 Studio에서 결과가 갱신된다.
- safe-auto가 invariant나 change budget을 넘으면 review로 전환된다.
- task의 `base_revision`이 달라진 proposal은 적용되지 않는다.

### Phase 6 — hardening과 release (P1)

구현:

- frontend unit test와 Playwright E2E
- 실제 FluidSynth/sfizz/FFmpeg 통합 test lane
- 장시간 project의 memory/performance 검사
- plugin package/bootstrap/cache-bust/reinstall 자동 검증
- crash, timeout, stale artifact, corrupt history 복구 test
- example을 sketch, refined, production 세 상태로 제공
- 기존 `visual-review` 내용은 Studio에 흡수하고 command는 호환 wrapper로 유지
- production render 경로는 `render.yaml` graph로 통일하고 legacy FluidSynth route는 호환용으로 유지

Exit criteria:

- Windows/Python 3.11 CI가 Python, TypeScript build/test, E2E를 모두 수행한다.
- 대표 plugin 흐름이 clean environment에서 재현된다.
- release wheel과 plugin asset에 동일한 Studio bundle과 schema가 들어간다.
- 사용자 문서가 실제 명령 및 화면과 일치한다.

## 10. 구현 단위와 예상 코드 위치

다음은 책임 경계이며 파일 이름은 구현 중 소폭 조정할 수 있다.

### Core

- `src/ledgerline/build_state.py`: revision, stage receipt, freshness, invalidation
- `src/ledgerline/jobs.py`: local subprocess job queue, progress, cancellation
- `src/ledgerline/studio_commands.py`: 공통 command validation과 transaction
- `src/ledgerline/history.py`: bounded disk history와 atomic restore
- `src/ledgerline/refinement.py`: pass coordinator와 report merge
- `src/ledgerline/harmony_analysis.py`
- `src/ledgerline/orchestration_analysis.py`
- 기존 `analysis.py`, `time_analysis.py`: production evidence 확장

현재 `studio_edits.py`는 command handler로 점진적으로 축소하고, `studio_server.py`에서 compile과
긴 작업 실행 책임을 분리한다.

### Studio UI

- `Transport`, `TimelineViewport`, `SelectionModel`
- `PianoRollEditor`, `VelocityLane`, `ControllerLane`, `TempoLane`
- `ScoreEditorBridge`
- `WaveformLane`, `SpectrumPanel`
- `MixerConsole`, `ProcessorEditor`, `EngineInspector`
- `CodexReviewPanel`, `DiffView`, `ABTransport`

canvas마다 독립 시간 계산을 하지 않고 공통 timeline/selection store를 사용한다. 대규모 상태
framework는 필수가 아니며 React context와 reducer로 시작한다.

### Plugin

- `compose-music/SKILL.md`: draft/refinement/Studio handoff 순서
- `musical-quality.md`: 여섯 pass와 checkpoint
- `cli-and-environment.md`: `studio --prepare`, refine, job 명령
- bootstrap smoke test: 실제 packaged wheel의 Studio/API schema 확인

## 11. 테스트 전략

### 11.1 음악 모델과 compiler

- explicit ID parsing, uniqueness, ID 준비, stability
- insert/delete/move/resize 후 voice duration과 tie consistency
- tuplets, grace/slur/hairpin은 Phase 3 안에서 지원 범위를 별도 schema test로 추가
- controls와 automation의 time/sample alignment
- multi-staff/voice round trip

### 11.2 engine truth

- score 변경, tempo 변경, mix 변경, instrument 변경별 invalidation matrix
- stale WAV 길이가 우연히 같아도 hash mismatch 검출
- partial render, failed render, cancellation, retry
- renderer/preset/state 변경 receipt
- process restart 뒤 job/history recovery

### 11.3 Studio

- component: selection, snap, command generation, meter calculation
- E2E: create/move/resize/delete/copy/paste/undo/redo
- E2E: CC/pedal/tempo edit와 score refresh
- E2E: edit → stale 표시 → render → waveform 갱신
- E2E: score click → timeline seek → audio/playhead sync
- 긴 project의 zoom/scroll과 memory ceiling

### 11.4 Codex plugin

- brief가 없을 때 방향 질문
- brief가 충분할 때 불필요한 전문 질문 없이 draft 시작
- unavailable instrument의 fail-closed 동작
- refinement proposal의 invariant와 budget 검증
- needs-direction round trip
- apply 후 changed-only render와 Studio refresh
- plugin reinstall 후 packaged runtime의 동일 동작

### 11.5 음악적 fixture

정답 곡을 강제하지 않는다. 의도적으로 문제가 있는 작은 fixture를 둔다.

- form: 대비 없는 반복과 미완성 ending
- harmony: voice crossing, low mud, resolution 후보
- orchestration: range 초과, 역할 경쟁, 지속적 tutti
- expression: 고정 velocity, 잘못된 pedal, 없는 keyswitch
- production: clipping, 지나친 저역, stale stem

분석기는 알려진 근거를 찾는지 검사하고, “좋은 곡”을 판정하는 test는 만들지 않는다.

## 12. migration과 호환성

- 0.5 project는 계속 읽고 compile/render할 수 있어야 한다.
- 기존 format 1은 유지하고 optional ID를 모르는 구 reader가 읽을 수 있는지는 지원 version으로
  명확히 제한한다.
- Studio의 구조 편집이 필요할 때만 `prepare-ids`를 안내한다.
- ID 준비는 원본 snapshot과 dry-run diff report를 만든다.
- build artifact는 ID만 추가되고 음악 event sequence가 동일하면 재생성 가능 상태로 표시하되,
  안전을 위해 최초 준비 후 compile manifest는 다시 만든다.
- 기존 delegation JSON은 읽되 새 상태와 impact fields는 optional로 시작한다.
- plugin manifest와 wheel version은 기능 phase release마다 함께 올린다.

## 13. 주요 위험과 단순 대응

| 위험 | 대응 |
|---|---|
| 분석 규칙이 특정 장르를 정답처럼 강제 | finding을 후보와 근거로 표현하고 style/brief를 입력으로 사용 |
| Studio preview와 offline mix 차이 | preview 지원 범위를 gain/pan/mute/solo로 명시하고 master는 재렌더 |
| 고품질 악기 부재 | engine panel에서 실제 설치 coverage를 보여주고 명시적 setup plan 사용 |
| native plugin 호환성 범위 폭증 | 기존 external adapter contract를 유지하고 알려진 adapter만 지원 |
| 편집 후 ID·selection 손실 | format 1 optional explicit ID와 transaction test |
| 긴 render가 UI를 정지 | 단순 local job runner와 polling, cancel |
| 자동 refinement가 원곡 정체성 훼손 | invariant, locked range, change budget, A/B 승인 |
| 기능이 늘며 CLI/Studio 계약 분리 | 하나의 command schema와 build state를 공유 |

## 14. Definition of Done

다음 scenario가 clean Windows 환경에서 처음부터 끝까지 성공하면 핵심 구현이 완료된 것이다.

1. 사용자가 비전문적인 언어로 목적, 느낌, 길이와 보존 요소를 말한다.
2. Codex plugin이 설치 악기를 확인하고 대표 초안을 실제 악기로 렌더한다.
3. 사용자의 방향 확인 뒤 전체 구조를 작성한다.
4. Codex가 최소 네 개의 음악 refinement pass와 production pass를 수행하고 이유를 기록한다.
5. Studio가 같은 revision의 악보, MIDI event, stem, mix, engine 상태를 연다.
6. 사용자가 piano roll에서 음표를 추가·이동·길이 조정하고 CC11과 pedal을 편집한다.
7. 해당 stem과 mix가 stale로 바뀌며, rebuild 후 실제 waveform과 meter가 갱신된다.
8. 사용자가 “후반부를 더 절박하게 하되 주선율은 보존”이라고 Codex에 요청한다.
9. Codex가 invariant를 지킨 proposal과 A/B 결과를 제공한다.
10. 사용자가 승인한 최종 revision을 lock·bundle하고 모든 asset/engine receipt를 남긴다.

이 scenario에 직접 기여하지 않는 대형 기능은 핵심 구현 이후로 미룬다.

## 15. 바로 시작할 작업 순서

1. format 1 optional explicit event ID와 `prepare-ids` 설계·test
2. `build/state.json` 및 invalidation matrix test
3. Studio transaction의 atomic write와 disk undo
4. local job runner와 `studio --prepare`
5. piano roll note CRUD·selection·resize
6. velocity/CC/pedal/tempo lane
7. plugin의 draft → refinement → Studio handoff 개정
8. structure/harmony/orchestration/expression report의 작은 fixture부터 구현
9. 실제 mixer meter와 instrument/engine panel
10. Playwright E2E와 packaged plugin smoke scenario

## 16. 실제 구현 매핑

초기 계획의 책임 분리는 더 작은 파일을 늘리는 대신 다음의 응집된 모듈로 구현했다.

| 책임 | 실제 구현 |
|---|---|
| revision, freshness, stage receipts | `src/ledgerline/build_state.py` |
| persistent local jobs and restart detection | `src/ledgerline/jobs.py` |
| command validation, atomic transaction, bounded disk history | `src/ledgerline/studio_edits.py` |
| Studio API and static application server | `src/ledgerline/studio_server.py` |
| Studio revision-matched view model | `src/ledgerline/studio_model.py` |
| evidence-only musical passes | `src/ledgerline/refinement.py` |
| bounded proposal preview and exact impact | `src/ledgerline/delegation_preview.py` |
| delegation/listening lifecycle | `src/ledgerline/delegation.py` |
| common Studio shell and transport | `studio-ui/src/StudioWorkbench.tsx` |
| piano roll and performance editing | `PianoRollEditor.tsx`, `PerformanceLane.tsx` |
| notation, mixer, engine truth, delegation review | `ScoreEditorView.tsx`, `MixerWorkbench.tsx`, `EngineStatusPanel.tsx`, `DelegationReviewPanel.tsx` |

계획에 있던 `studio_commands.py`, `history.py`, `harmony_analysis.py`,
`orchestration_analysis.py`는 별도 파일로 만들지 않았다. 현재 규모에서는 transaction과 history를
`studio_edits.py`에, 네 refinement 관점을 `refinement.py`에 함께 두는 편이 변경 원자성과 근거
형식을 유지하기 쉽다. 실행 중이던 외부 job은 재시작 뒤 이어서 실행하지 않으며, persistence를
읽어 명시적인 `failed/interrupted` 상태로 바꾼다.
