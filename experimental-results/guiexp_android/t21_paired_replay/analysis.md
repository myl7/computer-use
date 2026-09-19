# t21 paired replay analysis

c = replay discover cost, pw tokens, harness floor subtracted; d = deploy bill / p_in per served use (not floored, table parity); b = d + q*c.

| cell | n | valid | err | agent ok | q | c replay | c table | d | b=d+qc | paired share | table share | recomputed |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| z-ai/ContactsAddContact | 30 | 30 | 0 | 30 | 0.00 | 67,126 | 109,295 | 179 | 179 | 0.997 | 1.00 | 0.998 |
| z-ai/MarkorDeleteNote | 30 | 30 | 0 | 30 | 0.20 | 35,421 | 33,699 | 200 | 7,284 | 0.794 | 0.79 | 0.794 |
| z-ai/SimpleCalendarAddOneEvent | 30 | 30 | 0 | 14 | 0.00 | 535,319 | 590,145 | 558 | 558 | 0.999 | 1.00 | 0.999 |
| z-ai/OsmAndMarker | 30 | 30 | 0 | 12 | 0.00 | 258,911 | 278,230 | 475 | 475 | 0.998 | 1.00 | 0.998 |
| deepseek/ContactsAddContact | 30 | 30 | 0 | 27 | 0.03 | 67,267 | 116,031 | 430 | 2,672 | 0.960 | 0.96 | 0.963 |
| deepseek/MarkorDeleteNote | 30 | 30 | 0 | 23 | 0.03 | 22,338 | 10,762 | 402 | 1,147 | 0.949 | 0.93 | 0.929 |

## agent re-run noise (per cell, floored pw)

| cell | n | mean | stdev | CV | MAD | p10 | p90 | max/mean | ok-only CV |
|---|---|---|---|---|---|---|---|---|---|
| z-ai/ContactsAddContact | 30 | 67,126 | 21,893 | 0.33 | 12,330 | 47,397 | 110,415 | 1.80 | 0.33 |
| z-ai/MarkorDeleteNote | 30 | 35,421 | 13,221 | 0.37 | 10,098 | 19,876 | 47,833 | 2.05 | 0.37 |
| z-ai/SimpleCalendarAddOneEvent | 30 | 535,319 | 259,816 | 0.49 | 192,472 | 215,794 | 960,228 | 1.92 | 0.33 |
| z-ai/OsmAndMarker | 30 | 258,911 | 170,215 | 0.66 | 139,145 | 46,570 | 504,609 | 2.32 | 0.57 |
| deepseek/ContactsAddContact | 30 | 67,267 | 71,479 | 1.06 | 14,976 | 28,798 | 93,569 | 6.37 | 0.41 |
| deepseek/MarkorDeleteNote | 30 | 22,338 | 23,287 | 1.04 | 6,423 | 6,823 | 65,114 | 4.84 | 0.80 |

## within-binding paired difference c - b (pw, floored c, raw d)

| cell | median | CI lo | CI hi | IQR | program cheaper |
|---|---|---|---|---|---|
| z-ai/ContactsAddContact | 59,889 | 51,159 | 70,096 | 27,614 | 1.00 |
| z-ai/MarkorDeleteNote | 29,790 | 19,112 | 35,674 | 19,198 | 1.00 |
| z-ai/SimpleCalendarAddOneEvent | 451,237 | 280,889 | 685,284 | 469,366 | 1.00 |
| z-ai/OsmAndMarker | 240,183 | 106,917 | 358,850 | 282,774 | 1.00 |
| deepseek/ContactsAddContact | 53,235 | 38,943 | 59,333 | 31,075 | 1.00 |
| deepseek/MarkorDeleteNote | 14,923 | 9,189 | 20,649 | 13,079 | 1.00 |
