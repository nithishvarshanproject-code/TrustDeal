; learning.metta - D2: learn from human overrides, propose (never apply) policy changes.
; Loaded after policy.metta and rules.metta (uses allowed-max, margin-max, d-* accessors).
;
; Input:  ((override ID ORIGINAL NEW "reason" (deal ...)) ...)   from the DB via Python
; Output: ((policy-proposal tier-cap TIER OLD NEW (evidence ((ID "reason") ...))) ...)
;         or () when there is no pattern.
;
; v1 handles one direction: COUNTER -> APPROVE, which proposes raising that tier's cap.
; Only the tier cap can be proposed; the margin floor is never a candidate.

; Pattern needs at least this many matching overrides
(proposal-min-overrides 3)

(= (ov-id       (override $id $orig $new $reason $d)) $id)
(= (ov-orig     (override $id $orig $new $reason $d)) $orig)
(= (ov-new      (override $id $orig $new $reason $d)) $new)
(= (ov-reason   (override $id $orig $new $reason $d)) $reason)
(= (ov-deal     (override $id $orig $new $reason $d)) $d)

; How far the request was above what the policy allowed (recomputed with CURRENT policy,
; so an override that the current policy already covers has gap <= 0 and is not evidence).
(= (ov-gap $o) (let $d (ov-deal $o) (- (d-disc $d) (allowed-max $d))))

; Evidence for raising $tier's cap: COUNTER -> APPROVE, same record tier, a real gap,
; and a request a cap change CAN fix: within the category's margin limit, the category max
; and the escalation line. Only tier-cap is ever proposed: never a margin-floor(-for) or
; category-max fact.
(= (cap-evidence? $tier $o)
   (let $d (ov-deal $o)
     (and (and (== (ov-orig $o) COUNTER) (== (ov-new $o) APPROVE))
     (and (== (d-record $d) $tier)
     (and (> (ov-gap $o) (zero-float))
     (and (<= (d-disc $d) (margin-max $d))
     (and (<= (d-disc $d) (category-max-of (d-category $d)))
          (<= (d-disc $d) (policy escalation-line)))))))))

; Keep only the overrides that are evidence for $tier (one equation + if, see rules.metta).
(= (evidence-for $tier $os)
   (if (== $os ()) ()
       (let* (($h (car-atom $os)) ($rest (evidence-for $tier (cdr-atom $os))))
         (if (cap-evidence? $tier $h) (cons-atom $h $rest) $rest))))

(= (count-items $xs) (if (== $xs ()) 0 (+ 1 (count-items (cdr-atom $xs)))))

(= (max-gap $os)
   (if (== $os ()) (zero-float) (max2 (ov-gap (car-atom $os)) (max-gap (cdr-atom $os)))))

(= (evidence-list $os)
   (if (== $os ()) ()
       (let* (($h (car-atom $os)) ($id (ov-id $h)) ($reason (ov-reason $h))
              ($rest (evidence-list (cdr-atom $os))))
         (cons-atom ($id $reason) $rest))))

; New cap = old cap + the largest gap seen, never above the escalation line.
(= (proposal-for-tier $tier $os)
   (let* (($ev (evidence-for $tier $os)) ($n (count-items $ev)))
     (if (< $n (policy proposal-min-overrides))
         no-proposal
         (let* (($old (cap-of $tier))
                ($new (round1 (min2 (+ $old (max-gap $ev)) (policy escalation-line))))
                ($items (evidence-list $ev)))
           (if (> $new $old)
               (policy-proposal tier-cap $tier $old $new (evidence $items))
               no-proposal)))))

(= (proposals-for-tiers $tiers $os)
   (if (== $tiers ()) ()
       (let* (($p (proposal-for-tier (car-atom $tiers) $os))
              ($rest (proposals-for-tiers (cdr-atom $tiers) $os)))
         (if (== $p no-proposal) $rest (cons-atom $p $rest)))))

; Entry point: check every tier that exists in policy.metta.
(= (propose-policy-changes $os)
   (let $tiers (collapse (match &self (tier-cap $t $c) $t))
     (proposals-for-tiers $tiers $os)))
