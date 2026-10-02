; agent.metta - Deal Desk AGENT: what the agent does next, and the numbers of every offer.
; Loaded after policy.metta, rules.metta and learning.metta (it reuses their functions).
; Python only executes the action MeTTa returns; every discount and price comes from here.
;
; (next-action STATE DECISION EVENT)
;   STATE    NEW | WAITING_CUSTOMER | WAITING_VERIFICATION | ESCALATED | QUOTED | DECLINED | ORDERED | CLOSED
;   DECISION (verdict RESULT OFFER (deal ...))   RESULT/OFFER: the MeTTa decision for this deal
;                                                 (OFFER: its approved discount, or the last offer)
;   EVENT    (event KIND ROUND)   KIND: new-request customer-ask customer-accept customer-decline
;                                       verified not-verified manager-approved manager-rejected
;                                       order-placed tick
; -> (agent-action ACTION NEW-STATE RULE-ID OFFER)
;   ACTION create-quote | send-counter | send-decline | create-escalation-task |
;          request-verification | place-order | wait | close
;
; Agent rules (the RULE-ID in every activity line):
;   A1 APPROVE -> quote at the approved discount
;   A2 first contact, COUNTER, and the CLAIMED tier would approve the request -> verify the tier
;   A3 COUNTER -> counter at MeTTa's offer
;   A4 REJECT (below cost) -> counter at the allowed max if there is room, else decline
;   A5 ESCALATE -> task for a manager
;   A6 customer accepts the last offer -> quote at that offer
;   A7 customer declines -> close
;   A8 more than (agent-max-rounds) asks -> decline
;   A9 manager decides an escalation (the margin floor and category max still hold)
;   A10 order placed on an open quote -> order
;   A11 ordered or declined -> close
;   A12 anything else -> wait

(agent-max-rounds 3)
(quote-validity-hours 48)
; Suggest reaching the R4 volume threshold only to customers already ordering this many units.
(volume-suggest-from 50)

; ---------- helpers ----------
; Round UP to 0.1 (a discount that reaches a price target without overshooting the budget).
(= (ceil1 $x) (- (zero-float) (floor1 (- (zero-float) $x))))

; Largest discount the hard limits allow: margin floor and category max (never below 0).
(= (hard-limit-max $d)
   (floor1 (max2 (min2 (margin-max $d) (category-max-of (d-category $d))) (zero-float))))

(= (within-hard-limits $d $disc) (<= $disc (hard-limit-max $d)))

; Would the claimed tier, if verified, approve the request as asked?
(= (verification-helps $d)
   (if (has-conflict $d)
       (>= (floor1 (allowed-max-for $d (d-claimed $d))) (d-disc $d))
       False))

(= (agent-wait $state) (agent-action wait $state A12 None))

; ---------- acting on a MeTTa decision ----------
; $may-verify: True only on first contact (verification is not a negotiation tactic).
(= (act-on-decision $res $offer $d $may-verify)
   (if (== $res APPROVE)
       (agent-action create-quote QUOTED A1 $offer)
   (if (== $res ESCALATE)
       (agent-action create-escalation-task ESCALATED A5 None)
   (if (== $res COUNTER)
       (if (and $may-verify (verification-helps $d))
           (agent-action request-verification WAITING_VERIFICATION A2 None)
           (agent-action send-counter WAITING_CUSTOMER A3 $offer))
       ; REJECT: the ask is below cost; offer the allowed max when there is room
       (let $am (floor1 (allowed-max $d))
         (if (> $am (zero-float))
             (agent-action send-counter WAITING_CUSTOMER A4 $am)
             (agent-action send-decline DECLINED A4 None)))))))

; A manager approved an escalation: the requested discount, unless it breaks the margin
; floor or the category max; then the most those limits allow.
(= (act-on-approval $d)
   (let ($req $max) ((d-disc $d) (hard-limit-max $d))
     (if (<= $req $max)
         (agent-action create-quote QUOTED A9 $req)
         (agent-action send-counter WAITING_CUSTOMER A9 $max))))

; A manager rejected an escalation: counter at the normal allowed max.
(= (act-on-rejection $d) (agent-action send-counter WAITING_CUSTOMER A9 (floor1 (allowed-max $d))))

(= (open-state $state)
   (not (or (== $state CLOSED) (== $state ORDERED))))

; ---------- the next action ----------
(= (next-action $state (verdict $res $offer $d) (event $kind $round))
   (if (== $kind new-request)
       (if (== $state NEW) (act-on-decision $res $offer $d True) (agent-wait $state))
   (if (== $kind customer-ask)
       (if (== $state WAITING_CUSTOMER)
           (if (> $round (policy agent-max-rounds))
               (agent-action send-decline DECLINED A8 None)
               (act-on-decision $res $offer $d False))
           (agent-wait $state))
   (if (== $kind customer-accept)
       (if (== $state WAITING_CUSTOMER) (agent-action create-quote QUOTED A6 $offer) (agent-wait $state))
   (if (== $kind customer-decline)
       (if (open-state $state) (agent-action close CLOSED A7 None) (agent-wait $state))
   (if (or (== $kind verified) (== $kind not-verified))
       (if (== $state WAITING_VERIFICATION) (act-on-decision $res $offer $d False) (agent-wait $state))
   (if (== $kind manager-approved)
       (if (== $state ESCALATED) (act-on-approval $d) (agent-wait $state))
   (if (== $kind manager-rejected)
       (if (== $state ESCALATED) (act-on-rejection $d) (agent-wait $state))
   (if (== $kind order-placed)
       (if (== $state QUOTED) (agent-action place-order ORDERED A10 $offer) (agent-wait $state))
   (if (== $kind tick)
       (if (or (== $state ORDERED) (== $state DECLINED)) (agent-action close CLOSED A11 None) (agent-wait $state))
       (agent-wait $state)))))))))))

; ---------- offer numbers ----------
; (quote-terms DEAL OFFER) -> (terms OFFER UNIT-PRICE TOTAL SAVINGS VALID-HOURS)
;                          or (terms-refused hard-limits)
; The tag differs from the function name (MeTTa would otherwise re-evaluate it).
(= (quote-terms $d $offer)
   (if (within-hard-limits $d $offer)
       (let $unit (round2 (price-after $d $offer))
         (let $total (round2 (* $unit (d-qty $d)))
           (terms $offer $unit $total (round2 (- (* (d-list $d) (d-qty $d)) $total))
                  (policy quote-validity-hours))))
       (terms-refused hard-limits)))

; ---------- alternatives from the store's own catalog ----------
; The customer's budget is the unit price they asked for on the original product.
; A candidate passes only if the smallest discount that meets that budget is APPROVED by
; the normal decision (tier cap, bonuses, margin floor, category max, authority line).
; SAVINGS are against the ORIGINAL product's list price at the candidate's quantity.
; (alternatives ORIGINAL ORIGINAL-PRODUCT-ID ((candidate KIND PRODUCT-ID DEAL) ...))
;   -> ((alternative KIND PRODUCT-ID QTY OFFER UNIT-PRICE TOTAL SAVINGS) ...), at most 3.
; A bigger-quantity candidate (the R4 volume threshold) is added here when it applies.
(= (budget-unit $orig) (price-after $orig (d-disc $orig)))

(= (alternative-offer $orig $cand)
   (let $needed (max2 (ceil1 (* (- 1.0 (/ (budget-unit $orig) (d-list $cand))) 100.0)) (zero-float))
     (let ($res $x $e) (decide (with-disc $needed $cand))
       (if (== $res APPROVE)
           (let $unit (round2 (price-after $cand $needed))
             (let $total (round2 (* $unit (d-qty $cand)))
               (offer-ok $needed $unit $total (round2 (- (* (d-list $orig) (d-qty $cand)) $total)))))
           offer-none))))

(= (alt-one $orig (candidate $kind $pid $cand))
   (let $o (alternative-offer $orig $cand)
     (if (== $o offer-none)
         no-option
         (let (offer-ok $off $unit $total $sav) $o
           (alternative $kind $pid (d-qty $cand) $off $unit $total $sav)))))

(= (alt-each $orig $cands)
   (if (== $cands ()) ()
       (let ($h $rest) ((car-atom $cands) (alt-each $orig (cdr-atom $cands)))
         (let $r (alt-one $orig $h)
           (if (== $r no-option) $rest (cons-atom $r $rest))))))

(= (with-volume $orig $pid $cands)
   (let ($vmin $from) ((policy volume-min) (policy volume-suggest-from))
     (if (and (< (d-qty $orig) $vmin) (>= (d-qty $orig) $from))
         (cons-atom (candidate bigger-quantity $pid (with-qty $vmin $orig)) $cands)
         $cands)))

(= (alternatives $orig $pid $cands)
   (take-first 3 (alt-each $orig (with-volume $orig $pid $cands))))
