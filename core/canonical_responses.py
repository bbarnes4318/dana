"""Canonical response constants to prevent phrasing drift in prompts, state handlers, and runtime checks."""

DNC_CLOSE = "Understood. I’ll make a note of that. Take care."
WRONG_NUMBER_CLOSE = "Understood. I’ll make a note of that. Take care."
NOT_INTERESTED_CLOSE = "Understood. I won’t keep you. Take care."
REAL_PERSON_RESPONSE = "This is Alex with American Beneficiary. I’m just checking if you’re still open to looking at the final expense options."
LICENSED_RESPONSE = "No, I’m not the licensed agent. I just check the basics before they go over the actual options."
PRICE_RESPONSE = "The licensed agent has to go over that because it depends on your age, health, and location. I just check the basics before they review the options."
TRANSFER_FAILURE_CALLBACK = "Looks like I couldn’t get the licensed agent on the line right this second. I can have them call you back instead. Would later today or tomorrow work better?"
