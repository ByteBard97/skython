;BEGIN FRAGMENT CODE - Do not edit anything between this and the end comment
;NEXT FRAGMENT INDEX 4
Scriptname QF_SkythonTestQuest_01000800 Extends Quest Hidden
{Skython test data: the stage fragments of SkythonTestQuest. The VMAD written by testesp/builder binds
stage 10 -> Fragment_0, 20 -> Fragment_1, 100 -> Fragment_2, 200 -> Fragment_3.}

;BEGIN FRAGMENT Fragment_0
Function Fragment_0()
;BEGIN CODE
Skython.Send("skython_test", "stage_10")
;END CODE
EndFunction
;END FRAGMENT

;BEGIN FRAGMENT Fragment_1
Function Fragment_1()
;BEGIN CODE
Skython.Send("skython_test", "stage_20")
;END CODE
EndFunction
;END FRAGMENT

;BEGIN FRAGMENT Fragment_2
Function Fragment_2()
;BEGIN CODE
Skython.Send("skython_test", "stage_100")
;END CODE
EndFunction
;END FRAGMENT

;BEGIN FRAGMENT Fragment_3
Function Fragment_3()
;BEGIN CODE
Skython.Send("skython_test", "stage_200")
;END CODE
EndFunction
;END FRAGMENT

;END FRAGMENT CODE - Do not edit anything between this and the begin comment
