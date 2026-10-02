;BEGIN FRAGMENT CODE - Do not edit anything between this and the end comment
;NEXT FRAGMENT INDEX 1
Scriptname QF_SkythonTestQuest_01000800 Extends Quest Hidden
{VMAD spike: the stage 10 fragment of SkythonTestQuest. testesp/builder binds stage 10 -> Fragment_0 in the VMAD.}

;BEGIN FRAGMENT Fragment_0
Function Fragment_0()
;BEGIN CODE
Skython.Send("skython_test", "spike_stage_10")
;END CODE
EndFunction
;END FRAGMENT

;END FRAGMENT CODE - Do not edit anything between this and the begin comment
