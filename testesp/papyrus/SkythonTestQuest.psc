Scriptname SkythonTestQuest extends SkythonTestQuestBase
{Skython test data, attached to SkythonTestQuest (SkythonTest.esp|0x000800). Alias ids: 0 SkythonTarget,
1 SkythonItem, 2 SkythonExtra (reference aliases with SkythonTestAlias), 3 SkythonPlace (location alias with
SkythonTestLocationAlias).}

; Initial values are fixed so the tests can assert them. Target has no Papyrus initializer: the quest's VMAD sets it to
; SkythonTestNPC (SkythonTest.esp|0x000804), see TestEsp.cs.
Int Property Counter = 7 Auto
Float Property Ratio = 0.5 Auto
Bool Property Flag = True Auto
String Property Label = "initial label" Auto
Actor Property Target Auto

; A full (non-auto) property: Skython refuses get/set_property on it before anything runs.
Int Property Computed
	Int Function Get()
		Return Counter * 2
	EndFunction
EndProperty

; The test scenario: start the quest, fill the aliases, show the objective.
Function SetupScenario(Actor akTarget, ObjectReference akItem, Location akPlace, String asLabel)
	If !IsRunning()
		Start()
	EndIf
	Target = akTarget
	Label = asLabel
	(GetAlias(0) as ReferenceAlias).ForceRefTo(akTarget)
	(GetAlias(1) as ReferenceAlias).ForceRefTo(akItem)
	(GetAlias(3) as LocationAlias).ForceLocationTo(akPlace)
	SetStage(10)
	SetObjectiveDisplayed(10, True)
EndFunction

Int Function GetCounter()
	Return Counter
EndFunction

Int Function GetBaseCounter()
	Return BaseCounter
EndFunction

; Papyrus-side reads of the Float and Bool properties (the tests check set_property through them, not only through
; get_property, which reads the same variable index it wrote).
Float Function GetRatio()
	Return Ratio
EndFunction

Bool Function GetFlag()
	Return Flag
EndFunction

; Vanilla calls only (no SKSE script sources needed to compile): "none", "foreign", "empty" or "filled".
String Function DescribeAlias(ReferenceAlias akAlias)
	If akAlias == None
		Return "none"
	EndIf
	If akAlias.GetOwningQuest() != Self
		Return "foreign"
	EndIf
	If akAlias.GetReference() == None
		Return "empty"
	EndIf
	Return "filled"
EndFunction

; The same four answers for a location alias argument (the alias-argument path with a LocationAlias).
String Function DescribeLocationAlias(LocationAlias akAlias)
	If akAlias == None
		Return "none"
	EndIf
	If akAlias.GetOwningQuest() != Self
		Return "foreign"
	EndIf
	If akAlias.GetLocation() == None
		Return "empty"
	EndIf
	Return "filled"
EndFunction

; An argument typed as our own alias script: returns its Tag (5 on alias 0, or the value a test wrote).
Int Function AliasTag(SkythonTestAlias akAlias)
	Return akAlias.Tag
EndFunction

Int Function BumpCounter(Int n)
	Counter += n
	Return Counter
EndFunction
