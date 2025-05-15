
Make a python GUI terminal emulator with pyqt6 that has rows and columns of buttons at the top,some buttons can be set with a custom terminal commands that run when pressed. It should use a pseudo terminal and just make a UI to display everything. Use monospaced font. Use git bash if possible but if not, regular windows cmd or powershell.

ssh -i /c/Users/Basic/.ssh/oracle-amp.key ubuntu@193.122.157.24 
button text should be "SSH into amp"

that button should be in a section dedicated to running commands like that, then another section for simulated typing text - for faster typing common things i do everyday like:
one button when pressed should type "docker logs -f e5a209c3bb2c" (plus enter)
another button will type "sudo tail -f /var/log/apache2/access.log" (plus enter)

another button "python -m venv venv" (no enter afterwards)
another "source venv/Scripts/activate" (no enter)
another "pip install -r requirements.txt"

---

"C:\Program Files\Git\git-bash.exe" is the command for opening a git bash


--

This program should open the gui when ran, and just sit idle waiting for the user to press any buttons. it should stay open until closed. Put a close box in the window like a normal window has. Make the window large enough or automatic sizing, with manual way to enlarge/make it smaller.

Make a requirements.txt file and i will create a venv and install the dep's. I should be able to type python filename.py and it will open a window with 2 terminals, stacked vertically, with a checkbox or toggle or some way to select just one of the terminals, which will be the one used to paste/type text into if the user presses one of those buttons to print a line/command.

It should be able to spawn pseudo terminals and communicate in/out to them, and display the output in their own separate windows.