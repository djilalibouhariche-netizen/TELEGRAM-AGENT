"""
Tools the agent can call.
Add your own by (1) describing them in TOOLS and (2) handling their name
in execute_tool(). Keep each tool's `input_schema` accurate — Claude relies
on it to know what arguments to pass.

execute_tool() can return either:
  - a plain string (the tool result), or
  - a dict {"text": "...", "attachment": "/path/to/file"} when the tool
    produced a file that should be sent back to the user on Telegram.
"""

import datetime
import math
import os
import re
import smtplib
from email.mime.text import MIMEText

import requests
from duckduckgo_search import DDGS
from fpdf import FPDF
from pypdf import PdfReader
from openpyxl import Workbook, load_workbook

import hr_store

FILES_DIR = "/tmp/bot_files"
os.makedirs(FILES_DIR, exist_ok=True)


def _safe_filename(name: str, default_ext: str) -> str:
    name = re.sub(r"[^A-Za-z0-9_.-]", "_", name).strip("_") or "file"
    if not name.lower().endswith(default_ext):
        name += default_ext
    return name


TOOLS = [
    # ---- General utility tools -------------------------------------------------
    {
        "name": "get_current_time",
        "description": "Get the current date and time (UTC).",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "calculator",
        "description": "Evaluate a basic arithmetic expression, e.g. '12 * (3 + 4)'.",
        "input_schema": {
            "type": "object",
            "properties": {
                "expression": {
                    "type": "string",
                    "description": "A math expression using + - * / ( ) and numbers.",
                }
            },
            "required": ["expression"],
        },
    },
    {
        "name": "get_weather",
        "description": "Get the current weather for a city name.",
        "input_schema": {
            "type": "object",
            "properties": {
                "city": {"type": "string", "description": "City name, e.g. 'Blida' or 'Paris'."}
            },
            "required": ["city"],
        },
    },
    {
        "name": "web_search",
        "description": (
            "Search the public internet for current information (news, facts, "
            "prices, anything not already known). Returns a short list of "
            "results with titles, snippets and links."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string", "description": "The search query."}},
            "required": ["query"],
        },
    },
    # ---- Files: PDF / Excel ------------------------------------------------------
    {
        "name": "create_pdf",
        "description": "Create a simple PDF document with a title and body text, and send it to the user.",
        "input_schema": {
            "type": "object",
            "properties": {
                "filename": {"type": "string", "description": "Desired file name, e.g. 'report.pdf'."},
                "title": {"type": "string"},
                "content": {"type": "string", "description": "The body text of the PDF."},
            },
            "required": ["filename", "title", "content"],
        },
    },
    {
        "name": "read_pdf",
        "description": (
            "Extract and return the text content of a PDF file the user has "
            "uploaded. Use the local file path given to you in the conversation "
            "when the user sent the file."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"file_path": {"type": "string"}},
            "required": ["file_path"],
        },
    },
    {
        "name": "create_excel",
        "description": "Create a simple Excel spreadsheet from column headers and rows of data, and send it to the user.",
        "input_schema": {
            "type": "object",
            "properties": {
                "filename": {"type": "string", "description": "Desired file name, e.g. 'data.xlsx'."},
                "headers": {"type": "array", "items": {"type": "string"}},
                "rows": {
                    "type": "array",
                    "items": {"type": "array", "items": {"type": "string"}},
                    "description": "Each inner array is one row, matching the headers.",
                },
            },
            "required": ["filename", "headers", "rows"],
        },
    },
    {
        "name": "read_excel",
        "description": (
            "Read and return the contents of an Excel file the user has "
            "uploaded. Use the local file path given to you in the conversation."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"file_path": {"type": "string"}},
            "required": ["file_path"],
        },
    },
    # ---- Email --------------------------------------------------------------------
    {
        "name": "send_email",
        "description": "Send an email on the user's behalf.",
        "input_schema": {
            "type": "object",
            "properties": {
                "to": {"type": "string"},
                "subject": {"type": "string"},
                "body": {"type": "string"},
            },
            "required": ["to", "subject", "body"],
        },
    },
    # ---- Reminders ------------------------------------------------------------------
    {
        "name": "set_reminder",
        "description": (
            "Schedule a reminder message to be sent back to this same Telegram "
            "chat at a specific future date/time (UTC)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "message": {"type": "string"},
