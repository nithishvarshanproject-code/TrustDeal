% compat_petta.pl - PeTTa (Omega's MeTTa engine) compatibility shim.
%
% hyperon has format-args built in; PeTTa does not, so the trail sentences in rules.metta
% would stay unevaluated. This file adds it. It is loaded ONLY by the Omega plugin, on
% PeTTa, before policy.metta / rules.metta / learning.metta:
%   !(import! &self (library lib_import))
%   !(import_prolog_functions_from_file <path>/compat_petta.pl (format-args))
% hyperon never loads it. No business logic here: text formatting only.
%
% Behaviour matches hyperon: each "{}" is replaced by the next argument, left to right;
% strings are inserted without quotes, numbers and symbols as written.

'format-args'(Template, Args, Out) :-
    fill_placeholders(Template, Args, Out).

fill_placeholders(Text, [], Text) :- !.
fill_placeholders(Text, [Arg|Rest], Out) :-
    (   sub_string(Text, Before, 2, After, "{}")
    ->  sub_string(Text, 0, Before, _, Prefix),
        Start is Before + 2,
        sub_string(Text, Start, After, 0, Suffix),
        render_arg(Arg, Rendered),
        fill_placeholders(Suffix, Rest, FilledSuffix),
        atomics_to_string([Prefix, Rendered, FilledSuffix], Out)
    ;   Out = Text
    ).

render_arg(A, S) :- string(A), !, S = A.
render_arg(A, S) :- number(A), !, number_string(A, S).
render_arg(A, S) :- atom(A), !, atom_string(A, S).
render_arg(A, S) :- swrite(A, S).
