# 威科夫二世方法参考

This reference distills the user's prompt and the supplied Chinese EPUB `/Users/chenxingyu/Downloads/威科夫操盘法.epub`. Use it as a compact operating guide, not as a substitute for independent chart reading.

## Core Lens

- Treat price, volume, and speed as the primary evidence. Moving averages are context aids, not trading signals.
- Read in this order: background -> price/volume form -> character of behavior -> conclusion/probable intent -> action/risk.
- Use Wyckoff's three laws:
  - Supply and demand: rising price needs demand absorbing supply; falling price needs supply overwhelming demand.
  - Cause and effect: long trading ranges create the cause for later markup/markdown; bigger ranges imply bigger potential moves.
  - Effort and result: volume is effort; spread/progress is result. High effort with poor progress warns of absorption or hidden opposition.
- Prefer "what must be true for this interpretation?" over naming patterns. Mark only events supported by location, volume, spread, and follow-through.
- Do not force a five-phase map. If the chart only supports Phase A-C, stop there.

## Accumulation Map

Use this map when the background is bearish or depressed, then price begins to stop falling and trade sideways.

- Phase A: prior downtrend stops. Look for PS, SC, AR, and ST.
- Phase B: cause is built. Price oscillates inside the range; weak hands are worn out; volume may alternate between tests and absorption.
- Phase C: decisive test. Look for Spring, shakeout, or a final low-volume test that shows supply exhaustion.
- Phase D: demand shows control. Look for SOS/JAC, rising lows, and LPS pullbacks that hold above important support.
- Phase E: markup leaves the range. Price accepts above resistance and pullbacks should be shallow relative to advances.

Key events:

- PS: preliminary support; downside spread/volume expands and demand begins to appear, but the downtrend is not yet stopped.
- SC: selling climax; panic or forced selling after a decline, typically wide spread and very high volume. Interpret as large demand meeting public fear, not as automatic proof of a bottom.
- AR: automatic rally after SC; helps define the upper boundary of the initial trading range. Do not treat AR itself as the safest entry.
- ST: secondary test; revisits the SC area with less downside progress and preferably reduced volume/spread, showing supply has weakened.
- Spring: price briefly penetrates support and quickly returns above it. It is strongest when follow-through shows demand and later tests show supply is scarce.
- Test: a low-volume, narrow-spread revisit of Spring/SC/support. It asks whether meaningful supply remains.
- SOS/JAC: sign of strength / jump across the creek; demand pushes through resistance with spread and/or volume, then should hold above or near the old resistance.
- LPS: last point of support; a reaction after SOS that holds higher than prior danger points, often near old resistance or moving averages.

## Distribution Map

Use this map when the background is bullish or extended, then upward progress deteriorates and price begins to trade sideways.

- Phase A: prior uptrend stops. Look for buying climax, AR/SOW, and ST.
- Phase B: cause for markdown is built. Price oscillates; public demand still appears, but supply repeatedly caps progress.
- Phase C: final test of demand. Look for UT/UTAD or failed breakout above resistance.
- Phase D: supply shows control. Look for SOW, breaks of support/ice, weak rallies, and lower highs.
- Phase E: markdown leaves the range. Price accepts below support and rallies fail.

Key events:

- BC: buying climax; public demand becomes emotional and CM can distribute into strength.
- UT: upthrust; price pushes above resistance and returns back into the range. It needs bearish follow-through to matter.
- UTAD: upthrust after distribution; a late false breakout after a mature distribution range, often Phase C.
- SOW: sign of weakness; wide downside progress or support break showing supply control.
- Ice / break of ice: support line or key range floor. A decisive break with supply shifts the background bearish.
- LPSY: last point of supply; weak rally after SOW/breakdown that cannot reclaim important resistance.
- SOT: shortening of thrust; new highs make less progress, warning that demand is losing power or supply is increasing.

## Range Drawing Rules

- Accumulation range height: use Phase B's dense closing-price band, not the SC tail or AR extreme. A practical default is the 15th-85th percentile of Phase B closes, then adjust to obvious congestion.
- Distribution range height: use the dense Phase B closing-price band, not a single emotional high/low.
- Horizontal range span: start at SC/BC when the stopping action begins; end at the latest valid SOS/JAC for accumulation or SOW/breakdown for distribution. If no valid exit exists, end at the latest bar and mark the structure as unfinished.
- Draw multiple ranges if the chart clearly contains separate completed or active structures.

## Annotation Style

- Use Chinese labels in the form `[术语] + [理由]`.
- Keep each reason short, concrete, and tied to evidence visible on the chart: date, price zone, volume expansion/contraction, spread, support/resistance, moving averages, or follow-through.
- Use a measured Wyckoff voice: confident, skeptical of public emotion, focused on the Composite Man/CM, but avoid claiming certainty.
- Include "预测/推演" only as scenario analysis. Always state the invalidation point or what evidence would negate the read.
- Avoid investment advice language such as guaranteed returns, must buy, all-in, or certain profit.

## CSV Reading Expectations

- Accept common English and Chinese columns: date/日期/交易日期, open/开盘, high/最高, low/最低, close/收盘/收盘价, volume/成交量/vol.
- Convert dates, sort ascending, and use the most recent 500 rows by default.
- Compute MA50 and MA200 on the full sorted dataset before slicing to the display window.
- If volume is unavailable, say the read is price-structure only and mark volume-based events with lower confidence.

## Chart Requirements

- Main plot: black close line, blue dashed MA50, red dashed MA200.
- Use a volume subplot when volume exists.
- Detect and load a local Chinese font before plotting; on macOS prefer PingFang, Heiti, Songti, Hiragino Sans GB, or Noto CJK.
- Use pale green accumulation shading and pale red distribution shading.
- Divide phases with thick black dashed vertical lines and large red phase labels above the range.
- Put longer reasons in nearby whitespace with wrapped text and arrows. Never cover the most important price action.
- Finish by checking: CSV parse, date order, MA calculations, event locations, range boundaries, Chinese font rendering, and output image existence.
