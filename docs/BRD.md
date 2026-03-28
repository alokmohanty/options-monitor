# Context
- I have a options trading bot running on AWS EC2 instance. '
- The root folder path of the trading bot is /home/ubuntu/options-bot
- The log file path in the trading bot is - /home/ubuntu/options-bot/logs/cron_output.log

# Requirement
- build a simple python application to create an agent
- The purpose of the agent is:
    - a chat agent using genmini 2.5 pro (configurable). 
    - This will facilitate conversion using discord channel. 
    - This agent should be able to provide information to question realted to the log file like checking for errors and trades.
    - It should be able to understand the trading strategy by reading the code for the trading bot from the root folder of the bot /home/ubuntu/options-bot
- we should have config file to store all required constants
- create a document on the steps to be performed setting up from doscord side.
- create a document for deployment steps (deployment in ec2)

# Details available 
- i have got my gemini api key
- package manager used is uv which is already installed. for any library that needs to be installed use uv add command