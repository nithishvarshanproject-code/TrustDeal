; policy.metta - Deal Desk policy FACTS
; Tier caps, bonuses, margin floor, thresholds. Loaded into the Omega agent.
; No logic here; see rules.metta. All percentages are decimals (avoid integer division).

; R2 tier caps (percent)
(tier-cap Gold 20.0)
(tier-cap Silver 10.0)
(tier-cap New 5.0)

; R1 minimum margin after discount (percent of sale price).
; margin-floor is the default for any category without its own margin-floor-for fact.
(margin-floor 15.0)

; Category rules: each product category has its own margin floor and a maximum discount.
; (margin-floor-for CATEGORY PERCENT) replaces margin-floor for that category.
(margin-floor-for mobiles 8.0)
(margin-floor-for laptops 10.0)
(margin-floor-for accessories 30.0)
(margin-floor-for general 15.0)
; (category-max CATEGORY PERCENT): the allowed max never goes above this.
(category-max mobiles 12.0)
(category-max laptops 15.0)
(category-max accessories 40.0)
(category-max general 100.0)

; R3 payment bonus: 0 late payments over at least this many orders
(payment-bonus 2.0)
(payment-min-orders 10)

; R4 volume bonus
(volume-bonus 5.0)
(volume-min 100)

; R6 requests above this always go to a human
(escalation-line 25.0)

; R7 this many requests in a month (or more) triggers the flag
(repeat-limit 3)

; Confidence starts at 1.0; each issue subtracts its penalty
(penalty tier-conflict 0.1)
(penalty unverified-quote 0.1)
(penalty repeat-requests 0.2)

; D3 seller trust (replaces the old fixed missing-history penalty)
; confidence = orders / (orders + trust-prior-weight)
(trust-prior-weight 10.0)
; trust confidence below this lowers decision confidence by max-penalty * (1 - confidence)
(trust-min-confidence 0.5)
(trust-max-penalty 0.2)
