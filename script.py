#!/usr/bin/env python3
"""
Telegram Daily Poll Bot
Run this script daily via cron job to post polls and collect votes
Uses update_id storage for reliable message processing
"""

import json
import os
from datetime import datetime, timedelta
from typing import Dict, List, Set, Optional
import requests
import logging

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('poll_bot.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

class TelegramPollBot:
    def __init__(self, bot_token: str, chat_id: str):
        """
        Initialize the bot with token and chat ID
        
        Args:
            bot_token: Bot token from BotFather
            chat_id: Chat ID where polls will be posted
        """
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.base_url = f"https://api.telegram.org/bot{bot_token}"
        self.data_file = "poll_data.json"
        
    def load_data(self) -> Dict:
        """Load existing data from JSON file"""
        if os.path.exists(self.data_file):
            try:
                with open(self.data_file, 'r') as f:
                    data = json.load(f)
                    # Convert processed_polls back to set if it's a list
                    if isinstance(data.get("processed_polls"), list):
                        data["processed_polls"] = set(data["processed_polls"])
                    return data
            except (json.JSONDecodeError, FileNotFoundError):
                logger.warning("Could not load existing data, starting fresh")
        
        return {
            "current_week_start": None,
            "active_polls": {},  # poll_id: {"message_id": int, "date": str}
            "votes": {},  # user_id: total_score
            "processed_polls": set(),  # Set of poll_ids we've already processed
            "last_update_id": 0  # Track last processed update
        }
    
    def save_data(self, data: Dict) -> None:
        """Save data to JSON file"""
        # Convert set to list for JSON serialization
        data_to_save = data.copy()
        if isinstance(data_to_save.get("processed_polls"), set):
            data_to_save["processed_polls"] = list(data_to_save["processed_polls"])
        
        with open(self.data_file, 'w') as f:
            json.dump(data_to_save, f, indent=2)
    
    def get_week_start(self, date: datetime) -> str:
        """Get the Monday of the week for a given date"""
        days_since_monday = date.weekday()
        monday = date - timedelta(days=(days_since_monday + 1))
        return monday.strftime("%Y-%m-%d")
    
    def send_request(self, method: str, params: Dict) -> Dict:
        """Send request to Telegram API"""
        url = f"{self.base_url}/{method}"
        response = requests.post(url, json=params)
        response.raise_for_status()
        return response.json()
    
    def post_poll(self) -> tuple[str, int]:
        """Post daily poll and return poll ID and message ID"""
        now = datetime.now()
        day_name = now.strftime("%A")
        full_date = now.strftime("%B %d, %Y")
        
        question = f"{day_name}, {full_date}"
        options = ["1", "2", "3", "4", "5", "6"]
        
        params = {
            "chat_id": self.chat_id,
            "question": question,
            "options": options,
            "is_anonymous": False,
            "allows_multiple_answers": False
        }
        
        try:
            response = self.send_request("sendPoll", params)
            poll = response["result"]["poll"]
            message_id = response["result"]["message_id"]
            
            logger.info(f"Posted poll for {question} (Poll ID: {poll['id']})")
            return poll["id"], message_id
        except Exception as e:
            logger.error(f"Failed to post poll: {e}")
            raise
    
    def get_new_updates(self, last_update_id: int) -> List[Dict]:
        """Get new updates since last_update_id"""
        try:
            params = {
                "offset": last_update_id + 1,
                "limit": 100,
                "timeout": 10
            }
            response = self.send_request("getUpdates", params)
            return response.get("result", [])
        except Exception as e:
            logger.error(f"Failed to get updates: {e}")
            return []
    
    def process_updates(self, updates: List[Dict], active_polls: Set[str]) -> tuple[Dict[str, int], int]:
        """
        Process updates and extract poll answers for active polls
        Returns tuple of (user_votes, highest_update_id)
        """
        user_votes = {}
        highest_update_id = 0
        
        for update in updates:
            update_id = update.get("update_id", 0)
            highest_update_id = max(highest_update_id, update_id)
            
            # Check if this update contains a poll answer
            if "poll_answer" in update:
                poll_answer = update["poll_answer"]
                poll_id = poll_answer["poll_id"]
                
                # Only process votes for our active polls
                if poll_id in active_polls:
                    user = poll_answer["user"]
                    user_id = str(user["id"])
                    username = user.get("username") or user.get("first_name", f"User_{user_id}")
                    
                    # Get the selected option (0-indexed, so add 1)
                    if poll_answer["option_ids"]:
                        vote_value = poll_answer["option_ids"][0] + 1
                        
                        # Create a unique key for this user-poll combination
                        vote_key = f"{username}_{poll_id}"
                        
                        if vote_key not in user_votes:
                            user_votes[vote_key] = {
                                "username": username,
                                "poll_id": poll_id,
                                "vote_value": vote_value
                            }
                            logger.info(f"Recorded vote: {username} voted {vote_value} on poll {poll_id}")
        
        return user_votes, highest_update_id
    
    def aggregate_votes(self, poll_votes: Dict[str, Dict], existing_votes: Dict[str, int]) -> Dict[str, int]:
        """Aggregate poll votes into user totals"""
        # Start with existing votes
        total_votes = existing_votes.copy()
        
        # Add new votes
        for vote_key, vote_data in poll_votes.items():
            username = vote_data["username"]
            vote_value = vote_data["vote_value"]
            
            if username not in total_votes:
                total_votes[username] = 0
            total_votes[username] += vote_value
        
        return total_votes
    
    def send_weekly_summary(self, votes: Dict[str, int]) -> None:
        """Send weekly summary of votes"""
        if not votes:
            message = "No votes recorded this week! 📊"
        else:
            message = "🗳️ Weekly Poll Summary:\n\n"
            
            # Sort by total score descending
            sorted_votes = sorted(votes.items(), key=lambda x: x[1], reverse=True)
            
            for username, total_score in sorted_votes:
                message += f"{username}: {total_score}\n"
        
        params = {
            "chat_id": self.chat_id,
            "text": message
        }
        
        try:
            self.send_request("sendMessage", params)
            logger.info("Sent weekly summary")
        except Exception as e:
            logger.error(f"Failed to send weekly summary: {e}")
    
    def clean_old_polls(self, data: Dict) -> None:
        """Remove polls older than 7 days from active polls"""
        current_date = datetime.now()
        polls_to_remove = []
        
        for poll_id, poll_info in data["active_polls"].items():
            poll_date_str = poll_info.get("date")
            if poll_date_str:
                try:
                    poll_date = datetime.strptime(poll_date_str, "%Y-%m-%d")
                    days_old = (current_date - poll_date).days
                    
                    if days_old > 7:
                        polls_to_remove.append(poll_id)
                        logger.info(f"Removing old poll {poll_id} from {poll_date_str}")
                except ValueError:
                    # Invalid date format, remove it
                    polls_to_remove.append(poll_id)
        
        for poll_id in polls_to_remove:
            del data["active_polls"][poll_id]
            data["processed_polls"].discard(poll_id)
    
    def run_daily_task(self) -> None:
        """Main daily task - post poll and process votes"""
        data = self.load_data()
        
        # Ensure processed_polls is a set
        if not isinstance(data.get("processed_polls"), set):
            data["processed_polls"] = set(data.get("processed_polls", []))
        
        now = datetime.now()
        current_week = self.get_week_start(now)
        current_date = now.strftime("%Y-%m-%d")
        if updates:
            logger.info(f"Processing {len(updates)} new updates")
            
            # Get set of active poll IDs
            active_poll_ids = set(data["active_polls"].keys())
            
            # Process updates and get poll votes
            poll_votes, highest_update_id = self.process_updates(updates, active_poll_ids)
            
            # Update the last processed update ID
            if highest_update_id > data.get("last_update_id", 0):
                data["last_update_id"] = highest_update_id
                logger.info(f"Updated last_update_id to {highest_update_id}")
            
            # Aggregate votes into user totals
            if poll_votes:
                data["votes"] = self.aggregate_votes(poll_votes, data["votes"])
                logger.info(f"Processed {len(poll_votes)} new votes")
        else:
            logger.info("No new updates to process")
        # Check if it's a new week (or first run)
        if now.weekday() == 6 and:
            # If it's Sunday and we have data from previous week, send summary
            if  data.get("votes"):  # Sunday = 6
                logger.info("It's Sunday - sending weekly summary")
                self.send_weekly_summary(data["votes"])
            
            # Start new week
            logger.info(f"Starting new week: {current_week}")
            # Keep last_update_id to maintain continuity
            last_update_id = data.get("last_update_id", 0)
            data = {
                "current_week_start": current_week,
                "active_polls": {},
                "votes": {},
                "processed_polls": set(),
                "last_update_id": last_update_id
            }
        
        # Get new updates since last processed update
        logger.info(f"Getting updates since update_id {data.get('last_update_id', 0)}")
        updates = self.get_new_updates(data.get("last_update_id", 0))
        # Post new poll
        logger.info("Posting new daily poll")
        try:
            poll_id, message_id = self.post_poll()
            data["active_polls"][poll_id] = {
                "message_id": message_id,
                "date": current_date
            }
            logger.info(f"Added poll {poll_id} to active polls")
            
        except Exception as e:
            logger.error(f"Failed to post daily poll: {e}")
        
        # Clean up old polls (keep only last 7 days)
        self.clean_old_polls(data)
        
        # Save updated data
        self.save_data(data)
        logger.info("Daily task completed")

def main():
    """Main function to run the bot"""
    # Get configuration from environment variables
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    
    if not bot_token or not chat_id:
        logger.error("Please set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID environment variables")
        return
    
    bot = TelegramPollBot(bot_token, chat_id)
    bot.run_daily_task()

if __name__ == "__main__":
    main()
