def handleTimerEvent():
	# ENG-001: one Press 1 sample into the MES engine. Never let an exception kill the timer.
	try:
		mes.engine.tick()
	except:
		import traceback
		system.util.getLogger("mes.engine").error("tick failed: " + traceback.format_exc())
