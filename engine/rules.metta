; rules.metta - Deal Desk decision LOGIC
; R1-R7, the tier conflict rule, and the decision order (see CLAUDE.md).
; Loaded into the Omega agent together with policy.metta.
;
; Input:  (deal record-tier claimed-tier cost list qty discount
;               late-payments total-orders competitor-price competitor-verified
;               requests-this-month seller-name category) ; unknown values are the symbol None
; Trail entry: (RULE-ID "check" value status)  status = pass | fail | warn | skip
;
; Performance notes (hyperon 0.2.x):
; - let* costs ~5 ms per binding; a single let with a tuple pattern,
;   (let ($a $b) (value-a value-b) body), binds many values for ~1 ms.
;   So values are bound with tuple lets, or nested lets when one depends on another.
; - Values used more than once (allowed-max parts, margin limit, R3 status) are
;   computed once and passed along.
; - `and` evaluates BOTH arguments; nested `if`s skip work that cannot matter.

; ---------- helpers ----------
(= (policy $name) (match &self ($name $v) $v))
(= (round1 $x) (/ (round-math (* $x 10.0)) 10.0))
; A computed float zero. A literal 0.0 is printed as 0 by hyperon (its atom index
; merges it with the integer 0 used elsewhere), but as 0.0 by PeTTa; a computed
; zero prints as 0.0 on both, so outputs are identical on hyperon and Omega.
(= (zero-float) (- 1.0 1.0))

; field accessors
(= (d-record    (deal $rt $ct $c $l $q $d $lp $to $cp $cv $rq $nm $cg)) $rt)
(= (d-claimed   (deal $rt $ct $c $l $q $d $lp $to $cp $cv $rq $nm $cg)) $ct)
(= (d-cost      (deal $rt $ct $c $l $q $d $lp $to $cp $cv $rq $nm $cg)) $c)
(= (d-list      (deal $rt $ct $c $l $q $d $lp $to $cp $cv $rq $nm $cg)) $l)
(= (d-qty       (deal $rt $ct $c $l $q $d $lp $to $cp $cv $rq $nm $cg)) $q)
(= (d-disc      (deal $rt $ct $c $l $q $d $lp $to $cp $cv $rq $nm $cg)) $d)
(= (d-late      (deal $rt $ct $c $l $q $d $lp $to $cp $cv $rq $nm $cg)) $lp)
(= (d-orders    (deal $rt $ct $c $l $q $d $lp $to $cp $cv $rq $nm $cg)) $to)
(= (d-comp      (deal $rt $ct $c $l $q $d $lp $to $cp $cv $rq $nm $cg)) $cp)
(= (d-verified  (deal $rt $ct $c $l $q $d $lp $to $cp $cv $rq $nm $cg)) $cv)
(= (d-requests  (deal $rt $ct $c $l $q $d $lp $to $cp $cv $rq $nm $cg)) $rq)
(= (d-name      (deal $rt $ct $c $l $q $d $lp $to $cp $cv $rq $nm $cg)) $nm)
(= (d-category-raw (deal $rt $ct $c $l $q $d $lp $to $cp $cv $rq $nm $cg)) $cg)

; ---------- Categories: each product category has its own margin floor and max discount ----------
; No category (None) means general. A category without its own floor uses margin-floor;
; without its own max it uses general's.
(= (d-category $d) (let $c (d-category-raw $d) (if (== $c None) general $c)))
(= (floor-for $cat)
   (let $fs (collapse (match &self (margin-floor-for $cat $f) $f))
     (if (== $fs ()) (policy margin-floor) (car-atom $fs))))
(= (category-max-of $cat)
   (let $ms (collapse (match &self (category-max $cat $m) $m))
     (if (== $ms ()) (match &self (category-max general $g) $g) (car-atom $ms))))
(= (floor-of $d) (floor-for (d-category $d)))
; What the decision card shows. The function name differs from the data tag it returns.
(= (category-profile-of $cat)
   (let ($f $m) ((floor-for $cat) (category-max-of $cat)) (category-profile $cat $f $m)))

; ---------- Conflict rule: record tier always wins ----------
(= (has-conflict $d)
   (and (not (== (d-claimed $d) None))
        (not (== (d-claimed $d) (d-record $d)))))

(= (rule-conflict $d)
   (let ($rt $ct) ((d-record $d) (d-claimed $d))
     (if (has-conflict $d)
         (CONFLICT (format-args "claimed tier {} != record tier {}; using record" ($ct $rt)) $rt warn)
         (CONFLICT "claimed tier matches record (or not claimed)" $rt pass))))

; ---------- R2 tier cap (always uses the given tier; callers pass the record tier) ----------
(= (cap-of $tier) (match &self (tier-cap $tier $c) $c))

(= (rule-r2 $tier)
   (R2 (format-args "tier cap for {}" ($tier)) (cap-of $tier) pass))

; ---------- R3 payment bonus: 0 late payments over 10+ orders -> +2 ----------
(= (history-missing $d) (or (== (d-late $d) None) (== (d-orders $d) None)))

(= (r3-status $d)
   (if (history-missing $d)
       skip
       (if (and (== (d-late $d) 0) (>= (d-orders $d) (policy payment-min-orders)))
           pass
           fail)))

(= (bonus-for-r3-status $s) (if (== $s pass) (policy payment-bonus) (zero-float)))
(= (bonus-r3 $d) (bonus-for-r3-status (r3-status $d)))

(= (rule-r3 $d)
   ; format-args does NOT evaluate its arguments, so bind them with let first
   (let ($s $late $orders) ((r3-status $d) (d-late $d) (d-orders $d))
     (if (== $s skip)
         (R3 "payment history missing; bonus not applied" None skip)
         (R3 (format-args "{} late payments in {} orders" ($late $orders)) (bonus-for-r3-status $s) $s))))

; ---------- R4 volume bonus: >= 100 units -> +5 ----------
(= (bonus-r4 $d) (if (>= (d-qty $d) (policy volume-min)) (policy volume-bonus) (zero-float)))

(= (rule-r4 $d)
   (let ($q $min) ((d-qty $d) (policy volume-min))
     (if (>= $q $min)
         (R4 (format-args "{} units >= {}" ($q $min)) (policy volume-bonus) pass)
         (R4 (format-args "{} units < {}" ($q $min)) (zero-float) fail))))

; ---------- R5 competitor match: only a VERIFIED quote counts ----------
; A verified quote becomes a candidate allowed max (its implied discount off list).
(= (comp-status $d)
   (if (== (d-comp $d) None)
       none
       (if (== (d-verified $d) True) verified unverified)))

(= (comp-discount $d) (* (- 1.0 (/ (d-comp $d) (d-list $d))) 100.0))

(= (comp-candidate $d) (if (== (comp-status $d) verified) (comp-discount $d) (zero-float)))

(= (rule-r5 $d)
   (let ($s $cp) ((comp-status $d) (d-comp $d))
     (case $s
       ((none (R5 "no competitor quote" None skip))
        (unverified (R5 (format-args "competitor price {} not verified; ignored" ($cp)) None skip))
        (verified (let $cd (round1 (comp-discount $d))
                    (R5 (format-args "verified competitor price {} = {}% off list" ($cp $cd)) $cd pass)))))))

; ---------- R1 margin floor: price after discount keeps the category's margin floor ----------
(= (price-after $d $disc) (* (d-list $d) (/ (- 100.0 $disc) 100.0)))
(= (margin-pct $d $disc)
   (let $p (price-after $d $disc) (* (/ (- $p (d-cost $d)) $p) 100.0)))
; largest discount that still keeps the floor: price >= cost / (1 - floor/100)
(= (margin-max $d)
   (* (- 1.0 (/ (d-cost $d) (* (d-list $d) (- 1.0 (/ (floor-of $d) 100.0))))) 100.0))

(= (rule-r1 $d)
   (let ($disc $floor $mx-raw) ((d-disc $d) (floor-of $d) (margin-max $d))
     (let $m-raw (margin-pct $d $disc)
       (let ($m $mx) ((round1 $m-raw) (round1 $mx-raw))
         (if (>= $m-raw $floor)
             (R1 (format-args "margin at {}% off is {}% >= {}% (max discount {}%)" ($disc $m $floor $mx)) $m pass)
             (R1 (format-args "margin at {}% off is {}% < {}% (max discount {}%)" ($disc $m $floor $mx)) $m fail))))))

; ---------- R6 authority: > 25% always goes to a human ----------
(= (over-authority $d) (> (d-disc $d) (policy escalation-line)))

(= (rule-r6 $d)
   (let ($disc $line) ((d-disc $d) (policy escalation-line))
     (if (> $disc $line)
         (R6 (format-args "request {}% > {}%: needs a human" ($disc $line)) $disc warn)
         (R6 (format-args "request {}% <= {}%: within agent authority" ($disc $line)) $disc pass))))

; ---------- R7 repeat requests: 3+ earlier requests this month -> flag ----------
(= (repeat-flag $d) (>= (d-requests $d) (policy repeat-limit)))

(= (rule-r7 $d)
   (let ($n $lim) ((d-requests $d) (policy repeat-limit))
     (if (>= $n $lim)
         (R7 (format-args "{} earlier requests this month >= {}: flagged" ($n $lim)) $n warn)
         (R7 (format-args "{} earlier requests this month < {}" ($n $lim)) $n pass))))

; ---------- D3 seller trust: truth value from payment evidence ----------
; strength   = on-time orders / all orders       (how reliable)
; confidence = orders / (orders + prior weight)  (how much evidence we have)
; No history at all -> (stv 0.5 (zero-float)): "no idea, and no evidence".
(= (stv-from-counts $pos $n)
   (if (<= $n 0)
       (stv 0.5 (zero-float))
       (stv (/ (* 1.0 $pos) $n) (/ (* 1.0 $n) (+ $n (policy trust-prior-weight))))))

(= (history-stv $late $orders)
   (if (or (== $late None) (== $orders None))
       (stv 0.5 (zero-float))
       (stv-from-counts (- $orders $late) $orders)))

(= (seller-stv $d) (history-stv (d-late $d) (d-orders $d)))
(= (seller-trust-of $d) (seller-trust (d-name $d) (seller-stv $d)))

; Low evidence -> bigger confidence reduction; enough evidence -> none.
(= (trust-penalty-for $c)
   (if (< $c (policy trust-min-confidence)) (* (policy trust-max-penalty) (- 1.0 $c)) (zero-float)))
(= (trust-penalty $d) (let (stv $s $c) (seller-stv $d) (trust-penalty-for $c)))

(= (round2 $x) (/ (round-math (* $x 100.0)) 100.0))

(= (rule-trust $d)
   (let (stv $s $c) (seller-stv $d)
     (let ($name $s2 $c2 $p $min)
          ((d-name $d) (round2 $s) (round2 $c) (round2 (trust-penalty-for $c)) (policy trust-min-confidence))
       (if (> $p (zero-float))
           (TRUST (format-args "seller-trust {} (stv {} {}): confidence {} < {} -> decision confidence -{}"
                               ($name $s2 $c2 $c2 $min $p)) (stv $s $c) warn)
           (TRUST (format-args "seller-trust {} (stv {} {}): enough evidence, no change"
                               ($name $s2 $c2)) (stv $s $c) pass)))))

; Revision after a completed deal: turn the stv back into counts
; (n = k*c/(1-c), positive = s*n), add the new outcome, and recompute.
(= (update-trust (stv $s $c) $outcome)
   (let $k (policy trust-prior-weight)
     (let $n (/ (* $k $c) (- 1.0 $c))
       (let ($pos $n2) ((* $s $n) (+ $n 1.0))
         (let $pos2 (if (== $outcome on-time) (+ $pos 1.0) $pos)
           (stv (/ $pos2 $n2) (/ $n2 (+ $n2 $k))))))))

; ---------- ALLOWED: min(max(cap + bonuses, verified competitor), margin limit, category max) ----------
; Never below 0: a product with no discount room in its category gets a COUNTER at 0% (list price).
(= (min2 $a $b) (if (< $a $b) $a $b))
(= (max2 $a $b) (if (> $a $b) $a $b))

; Every input to the allowed max, computed once:
; (parts cap r3-bonus r4-bonus competitor margin-limit category-max)
(= (allowed-parts $d $tier)
   (let ($cap $b3 $b4 $comp $mx $cmx)
        ((cap-of $tier) (bonus-r3 $d) (bonus-r4 $d) (comp-candidate $d) (margin-max $d)
         (category-max-of (d-category $d)))
     (parts $cap $b3 $b4 $comp $mx $cmx)))

; Before the "never below 0" rule (the ALLOWED line shows it when it matters)
(= (allowed-raw-from-parts (parts $cap $b3 $b4 $comp $mx $cmx))
   (min2 (min2 (max2 (+ $cap (+ $b3 $b4)) $comp) $mx) $cmx))

(= (allowed-from-parts $p) (max2 (allowed-raw-from-parts $p) (zero-float)))

; Takes a tier so the override hint can use the CLAIMED tier.
(= (allowed-max-for $d $tier) (allowed-from-parts (allowed-parts $d $tier)))
(= (allowed-max $d) (allowed-max-for $d (d-record $d)))

; The category max is named only when it is the limit that applies (never for general, max 100).
(= (rule-allowed-from (parts $cap $b3 $b4 $comp $mx $cmx) $am)
   (let ($sum $comp1 $mx1 $cmx1 $final $raw)
        ((+ $cap (+ $b3 $b4)) (round1 $comp) (round1 $mx) (round1 $cmx) (floor1 $am)
         (allowed-raw-from-parts (parts $cap $b3 $b4 $comp $mx $cmx)))
     (let ($limits $room)
          ((if (< $cmx (min2 (max2 $sum $comp) $mx))
               (format-args "margin limit {}, category max {}" ($mx1 $cmx1))
               (format-args "margin limit {}" ($mx1)))
           (if (< $raw (zero-float)) " (below 0: no discount room in this category)" ""))
       (ALLOWED
         (format-args "min(max(cap {} + R3 {} + R4 {} = {}, competitor {}), {}) = {}{}"
                      ($cap $b3 $b4 $sum $comp1 $limits $final $room))
         $final pass))))

(= (rule-allowed $d)
   (let $p (allowed-parts $d (d-record $d)) (rule-allowed-from $p (allowed-from-parts $p))))

; ---------- DECISION: the Decision order from CLAUDE.md, checked top to bottom ----------
; Returns (RESULT approved-discount DECISION-trail-entry)
; Counter offers are rounded DOWN (floor1) so they can never round past the margin limit.
; decide-with takes the deal's allowed max ($am, unrounded) so it is computed only once.
(= (decide $d) (decide-with $d (allowed-max $d)))

(= (decide-with $d $am)
   (let ($disc $cost $line $allowed) ((d-disc $d) (d-cost $d) (policy escalation-line) (floor1 $am))
     (let $price (price-after $d $disc)
       (if (< $price $cost)
           (REJECT None (DECISION (format-args "step 1: price {} < cost {} -> REJECT" ($price $cost)) REJECT fail))
       (if (> $disc $line)
           (ESCALATE None (DECISION (format-args "step 2: request {}% > {}% -> ESCALATE" ($disc $line)) ESCALATE warn))
       (if (<= $disc $am)
           (APPROVE $disc (DECISION (format-args "step 3: request {}% <= allowed {}% -> APPROVE" ($disc $allowed)) APPROVE pass))
       (if (<= $am (zero-float))
           (COUNTER $allowed (DECISION (format-args "step 4: request {}% but no discount room in this category -> COUNTER at {}% (list price)" ($disc $allowed)) COUNTER warn))
           (COUNTER $allowed (DECISION (format-args "step 4: request {}% > allowed {}% -> COUNTER at {}%" ($disc $allowed $allowed)) COUNTER warn)))))))))

; ---------- Impact simulator: the outcome of a deal and a check of the discount offered ----------
; Used to replay stored deals under a proposed policy (in an isolated engine instance).
; offer-check: would the discount OFFERED sell below cost or under the category's margin floor?
; A 0% offer is the list price, so it is never a discount breach.
(= (offer-check $d $disc)
   (if (<= $disc (zero-float))
       (price-ok margin-ok)
       (let $price (price-after $d $disc)
         ((if (< $price (d-cost $d)) below-cost price-ok)
          (if (< (margin-pct $d $disc) (floor-of $d)) margin-breach margin-ok)))))

; (outcome RESULT OFFERED-DISCOUNT PRICE-CHECK MARGIN-CHECK); the name differs from the tag.
(= (impact-outcome $d)
   (let ($result $disc $entry) (decide $d)
     (if (== $disc None)
         (outcome $result None no-offer no-offer)
         (let ($price-check $margin-check) (offer-check $d $disc)
           (outcome $result $disc $price-check $margin-check)))))

; ---------- Trust ranking: strength x confidence, so 2 perfect orders do not outrank 40 ----------
(= (trust-score (stv $s $c)) (* $s $c))

; ---------- Confidence: 1.0 minus a penalty for each issue (never below 0) ----------
; Missing/thin payment history is handled by trust-penalty (D3).
(= (penalty-if $cond $name) (if $cond (match &self (penalty $name $p) $p) (zero-float)))

(= (confidence $d)
   (let $c (- 1.0 (+ (penalty-if (has-conflict $d) tier-conflict)
                  (+ (trust-penalty $d)
                  (+ (penalty-if (== (comp-status $d) unverified) unverified-quote)
                     (penalty-if (repeat-flag $d) repeat-requests)))))
     (max2 (zero-float) (/ (round-math (* $c 100.0)) 100.0))))

; ---------- Override hint: what the answer would be with the CLAIMED tier ----------
; NOTE: the function name must differ from the data tag it returns (override-hint),
; otherwise MeTTa re-evaluates the result as a call to itself.
; Reuses the record-tier parts: only the cap differs for the claimed tier.
(= (compute-hint-with $d (parts $cap $b3 $b4 $comp $mx $cmx))
   (if (has-conflict $d)
       (let $ct (d-claimed $d)
         (let $claimed-cap (cap-of $ct)
           (override-hint verify-tier $ct
             (round1 (allowed-from-parts (parts $claimed-cap $b3 $b4 $comp $mx $cmx))))))
       (override-hint none)))

(= (compute-hint $d) (compute-hint-with $d (allowed-parts $d (d-record $d))))

; ---------- D1 What would it take: change ONE input, re-run the rules ----------
; Copies of the deal with one field changed (pattern matching rebuilds the deal).
(= (with-tier $t (deal $rt $ct $c $l $q $d $lp $to $cp $cv $rq $nm $cg))
   (deal $t $t $c $l $q $d $lp $to $cp $cv $rq $nm $cg))
(= (with-qty $n (deal $rt $ct $c $l $q $d $lp $to $cp $cv $rq $nm $cg))
   (deal $rt $ct $c $l $n $d $lp $to $cp $cv $rq $nm $cg))
(= (with-verified-quote (deal $rt $ct $c $l $q $d $lp $to $cp $cv $rq $nm $cg))
   (deal $rt $ct $c $l $q $d $lp $to $cp True $rq $nm $cg))
(= (with-disc $x (deal $rt $ct $c $l $q $d $lp $to $cp $cv $rq $nm $cg))
   (deal $rt $ct $c $l $q $x $lp $to $cp $cv $rq $nm $cg))

; Does the changed deal $d2 really pass? Re-run the decision at $target.
; The allowed max does not depend on the discount, so $d2's ($am2) is reused.
; Keep it only if: APPROVE and the R1 margin floor holds.
(= (approvable-at $d2 $am2 $target)
   (let ($res $x $e) (decide-with (with-disc $target $d2) $am2)
     (and (== $res APPROVE) (>= (margin-pct $d2 $target) (floor-of $d2)))))

; Offers are rounded DOWN to 0.1: at the exact margin limit (17.647...) float maths
; gives a margin of 14.999...%, which would (correctly) fail R1.
(= (floor1 $x) (/ (floor-math (* $x 10.0)) 10.0))

; $disc and $am are the ORIGINAL deal's request and allowed max.
; Only re-run the decision if the change improves the allowed max at all.
(= (try-change $type $detail $d2 $disc $am)
   (let $am2 (allowed-max $d2)
     (let $new (floor1 $am2)
       (if (> $new $am)
           (if (approvable-at $d2 $am2 (min2 $disc $new)) ($type $detail $new) no-option)
           no-option))))

(= (try-lower-discount $d $disc $am)
   (let $a (floor1 $am)
     (if (and (> $a (zero-float)) (< $a $disc))
         (if (approvable-at $d $am $a) (lower-discount $a $a) no-option)
         no-option)))

; List helpers. One equation each, with `if` on the empty list: two equations
; like (f ()) and (f $xs) would BOTH match () and give duplicate answers.
(= (drop-no-options $xs)
   (if (== $xs ()) ()
       (let ($h $rest) ((car-atom $xs) (drop-no-options (cdr-atom $xs)))
         (if (== $h no-option) $rest (cons-atom $h $rest)))))

; Named take-first (not take): OmegaClaw defines its own take in the same space.
(= (take-first $n $xs)
   (if (or (== $n 0) (== $xs ())) ()
       (let ($h $rest) ((car-atom $xs) (take-first (- $n 1) (cdr-atom $xs)))
         (cons-atom $h $rest))))

(= (what-if-options $d)
   (let ($am $disc $vmin) ((allowed-max $d) (d-disc $d) (policy volume-min))
     (let ($tier $comp $volume $lower)
          ((if (has-conflict $d)
               (let $ct (d-claimed $d) (try-change verify-tier $ct (with-tier $ct $d) $disc $am))
               no-option)
           (if (== (comp-status $d) unverified)
               (try-change verify-competitor (d-comp $d) (with-verified-quote $d) $disc $am)
               no-option)
           (if (< (d-qty $d) $vmin)
               (try-change raise-quantity $vmin (with-qty $vmin $d) $disc $am)
               no-option)
           (try-lower-discount $d $disc $am))
       (take-first 3 (drop-no-options ($tier $comp $volume $lower))))))

(= (compute-what-if $result $d)
   (if (== $result APPROVE) (what-if ()) (what-if (what-if-options $d))))

; ---------- Entry points ----------
; (decision RESULT DISCOUNT CONFIDENCE TRAIL OVERRIDE-HINT WHAT-IF)
; evaluate-core skips the (slow) what-if search: WHAT-IF is (what-if not-computed).
; The allowed-max parts are computed once and shared by DECISION, ALLOWED and the hint.
(= (evaluate-core $d)
   (let $p (allowed-parts $d (d-record $d))
     (let $am (allowed-from-parts $p)
       (let ($result $disc $decision-entry) (decide-with $d $am)
         (let $trail ((rule-conflict $d) (rule-r1 $d) (rule-r2 (d-record $d)) (rule-r3 $d)
                      (rule-r4 $d) (rule-r5 $d) (rule-r6 $d) (rule-r7 $d) (rule-trust $d)
                      (rule-allowed-from $p $am) $decision-entry)
           (decision $result $disc (confidence $d) $trail (compute-hint-with $d $p)
                     (what-if not-computed)))))))

; What-if options on their own (for GET /deals/{id}/what-if).
(= (what-if-for $d)
   (let ($result $disc $entry) (decide $d) (compute-what-if $result $d)))

; Full evaluation: the core decision plus what-if options.
(= (evaluate $d)
   (let (decision $result $disc $conf $trail $hint $w) (evaluate-core $d)
     (decision $result $disc $conf $trail $hint (compute-what-if $result $d))))
