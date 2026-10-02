Scriptname SkythonTestAlias extends ReferenceAlias
{Skython test data, on the reference aliases 0-2 of SkythonTestQuest.}

Int Property Tag = 5 Auto

; Killing the actor in an alias moves the quest to stage 100, whose fragment calls Skython.Send.
Event OnDeath(Actor akKiller)
	GetOwningQuest().SetStage(100)
EndEvent
