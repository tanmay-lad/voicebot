import asyncio
from asyncio import Queue
import websockets
from typing import AsyncGenerator
from dotenv import load_dotenv
import os
import json
import base64
import requests
import time
import subprocess
import shutil
import platform

from elevenlabs.client import ElevenLabs
from elevenlabs import stream

from groq import AsyncGroq

from deepgram import (
    DeepgramClient,
    DeepgramClientOptions,
    LiveTranscriptionEvents,
    LiveOptions,
    Microphone,
)

load_dotenv()

async def text_chunker(chunks):
    """Split text into chunks, ensuring to not break sentences."""
    splitters = (".", ",", "?", "!", ";", ":", "—", "-", "(", ")", "[", "]", "}", " ")
    buffer = ""

    async for text in chunks:
        if buffer.endswith(splitters):
            yield buffer + " "
            buffer = text
        elif text.startswith(splitters):
            yield buffer + text[0] + " "
            buffer = text[1:]
        else:
            buffer += text

    if buffer:
        yield buffer + " "

class TextToSpeech:
    # Set your Deepgram API Key and desired voice model
    # DG_API_KEY = os.getenv("DEEPGRAM_API_KEY")
    # MODEL_NAME = "aura-asteria-en"  # Example model name, change as needed

    elevenlabs_api_key = os.getenv("ELEVENLABS_API_KEY")
    model = "eleven_turbo_v2"  # Example model name, change as needed
    voice_id = '2zRM7PkgwBPiau2jvVXc'  # [HIN: 1qEiC6qsybMkmnNdVMbK, HIN: 50YSQEDPA2vlOxhCseP4, EN-IN: ftDdhfYtmfGP0tFlBYA1, EN-IN: 2zRM7PkgwBPiau2jvVXc, EN-US: 21m00Tcm4TlvDq8ikWAM]

    async def speak(self, text_iterator):
        start_time = time.time()  # Record the time before sending the request
        uri = f"wss://api.elevenlabs.io/v1/text-to-speech/{self.voice_id}/stream-input?model_id={self.model}"

        async with websockets.connect(uri) as websocket:

            await websocket.send(json.dumps({
                "text": " ",
                "voice_settings": {"stability": 0.5, "similarity_boost": 0.8},
                "xi_api_key": self.elevenlabs_api_key,
            }))

            start_time = None
            first_byte_time = None
            
            async def listen():
                """Listen to the websocket for audio data and stream it."""
                nonlocal first_byte_time
                while True:
                    try:
                        message = await websocket.recv()
                        data = json.loads(message)
                        if data.get("audio"):
                            if first_byte_time is None:
                                first_byte_time = time.time()
                                latency = int((first_byte_time - start_time) * 1000)
                                print(f"Time to first byte: {latency} ms")
                            yield base64.b64decode(data["audio"])
                        elif data.get('isFinal'):
                            break
                    except websockets.exceptions.ConnectionClosed:
                        print("Connection closed")
                        break

            async def append_audio_chunks():
                audio_chunks = []
                async for audio_chunk in listen():
                    audio_chunks.append(audio_chunk)
                return audio_chunks

            #end_time = time.time()  # Record the time before sending the request
            listen_task = asyncio.create_task(append_audio_chunks())

            async for text in text_chunker(text_iterator):
                if start_time is None:
                    start_time = time.time()
                await websocket.send(json.dumps({"text": text, "try_trigger_generation": True}))

            """
            # Send LLM response to eleven labs for tts
            llm_response = {
                "text": text,
                "try_trigger_generation": True
            }
            await websocket.send(json.dumps(llm_response))
            """

            # Send EOS message with an empty string instead of a single space as mentioned in the documentation
            eos_message = {
                "text": ""
            }
            await websocket.send(json.dumps(eos_message))

            audio_stream = await listen_task
            stream(audio_stream)
            
            #end_time = time.time()  # Record the time before sending the request
            #ttts = int((end_time - start_time) * 1000)  # Calculate the time for tts to complete
            #print(f"TTS Completion Time (TTTS): {ttts} ms\n")

class TranscriptCollector:
    def __init__(self):
        self.reset()

    def reset(self):
        self.transcript_parts = []

    def add_part(self, part):
        self.transcript_parts.append(part)

    def get_full_transcript(self):
        return ' '.join(self.transcript_parts)

transcript_collector = TranscriptCollector()

async def llm_tts(client, query, conversation_history) -> str:
    #client = AsyncGroq(api_key = os.getenv('GROQ_API_KEY'))

    #with open('system_prompt_hotel_booking.txt', 'r') as file:
    #    system_prompt = file.read().strip()

    start_time = time.time()

    stream = await client.chat.completions.create(
        #
        # Required parameters
        #
        messages = conversation_history + [
            # conversation_history includes system prompt and chat history
            
            # Set a user message for the assistant to respond to.
            {
                "role": "user",
                "content": query,
            },
        ],
        # The language model which will generate the completion.
        model="llama3-70b-8192",
        #
        # Optional parameters
        #
        # Controls randomness: lowering results in less random completions.
        # As the temperature approaches zero, the model will become
        # deterministic and repetitive.
        temperature=0.5,
        # The maximum number of tokens to generate. Requests can use up to
        # 2048 tokens shared between prompt and completion.
        max_tokens=1024,
        # Controls diversity via nucleus sampling: 0.5 means half of all
        # likelihood-weighted options are considered.
        top_p=1,
        # A stop sequence is a predefined or user-specified text string that
        # signals an AI to stop generating content, ensuring its responses
        # remain focused and concise. Examples include punctuation marks and
        # markers like "[end]".
        stop=None,
        # If set, partial message deltas will be sent.
        stream=True,
    )

    end_time = time.time()

    # Print the completion returned by the LLM.
    #print(chat_completion.choices[0].message.content)

    #response = chat_completion.choices[0].message.content
    elapsed_time = int((end_time - start_time) * 1000)
    print(f"LLM ({elapsed_time}ms): ")

    
    # Print the incremental deltas returned by the LLM.
    print("Assistant: ", end="")
    response = ""
    
    async def text_iterator():
        async for chunk in stream:
            #print(chunk.choices[0].delta.content, end="")
            #print(type(chunk))
            
            nonlocal response
            delta = chunk.choices[0].delta
            if delta.content:
                print(delta.content, end="")
                response += delta.content
                yield delta.content
            
            """
            for choice in chunk.choices:
                if choice.delta.content is not None:
                    print(choice.delta.content, end="")
                    words.append(choice.delta.content)
                    if choice.delta.content.endswith('.'):
                        sentence = ''.join(words)
                        #print(sentence)
                        paragraph += sentence + " "  # Add the sentence to the paragraph
                        #yield sentence
                        words = []
                else:
                    sentence = ''.join(words)
                    #print(sentence)
                    paragraph += sentence + " "  # Add the sentence to the paragraph
                    #yield sentence
                    words = []
            """

    tts = TextToSpeech()
    await tts.speak(text_iterator())
    print()

    return response

class ConversationManager:

    def __init__(self, client, conversation_history, sentence_queue):
        self.client = client
        self.conversation_history = conversation_history
        self.sentence_queue = sentence_queue

    async def on_message(self, event, result, **kwargs):
        sentence = result.channel.alternatives[0].transcript
        
        if not result.speech_final:
            transcript_collector.add_part(sentence)
        else:
            # This is the final part of the current sentence
            transcript_collector.add_part(sentence)
            full_sentence = transcript_collector.get_full_transcript()
            # Check if the full_sentence is not empty before printing
            if len(full_sentence.strip()) > 0:
                full_sentence = full_sentence.strip()
                print(f"Guest: {full_sentence}")
                if "goodbye" in full_sentence.lower():
                    #call_analytics.call_analytics(self.conversation_history)
                    with open('call_recording.json', 'w') as file:
                        json.dump(self.conversation_history, file, indent=4)
                    raise Exception("Guest hung up")
                await self.sentence_queue.put(full_sentence)
                transcript_collector.reset()    

    async def on_open(self, event, open, **kwargs):
        print(f"Connection Open")

    async def on_metadata(self, event, metadata, **kwargs):
        print(f"Metadata: {metadata}")

    async def on_speech_started(self, event, speech_started, **kwargs):
        print(f"Speech Started")

    async def on_utterance_end(self, event, utterance_end, **kwargs):
        print(f"Utterance End")

    async def on_close(self, event, close, **kwargs):
        print(f"Connection Closed")

    async def on_error(self, event, error, **kwargs):
        print(f"Handled Error: {error}")

    async def process_sentence_queue(self):
        while True:
            if not self.sentence_queue.empty():
                user_query = await self.sentence_queue.get()
                assistant_response = await llm_tts(self.client, user_query, self.conversation_history)
                # update conversation history
                self.conversation_history = self.conversation_history + [
                    {"role": "user", "content": user_query},
                    {"role": "assistant", "content": assistant_response},
                ]
            await asyncio.sleep(0.1)  # adjust the sleep time as needed    
    
    async def stt(self):
        config = DeepgramClientOptions(options={"keepalive": "true"})
        deepgram: DeepgramClient = DeepgramClient("", config)

        dg_connection = deepgram.listen.asynclive.v("1")
        print ("Listening...")

        dg_connection.on(LiveTranscriptionEvents.Open, self.on_open)
        dg_connection.on(LiveTranscriptionEvents.Transcript, self.on_message)
        dg_connection.on(LiveTranscriptionEvents.Metadata, self.on_metadata)
        dg_connection.on(LiveTranscriptionEvents.SpeechStarted, self.on_speech_started)
        dg_connection.on(LiveTranscriptionEvents.UtteranceEnd, self.on_utterance_end)
        dg_connection.on(LiveTranscriptionEvents.Close, self.on_close)
        dg_connection.on(LiveTranscriptionEvents.Error, self.on_error)

        options = LiveOptions(
            model="nova-2",
            punctuate=True,
            language="en-IN", #hi, hi-Latn, en-IN
            encoding="linear16",
            channels=1,
            sample_rate=16000,
            endpointing=400, #300, Time in milliseconds of silence to wait for before finalizing speech
            smart_format=True,
            numerals=True,
        )

        await dg_connection.start(options)

        # Open a microphone stream on the default input device
        microphone = Microphone(dg_connection.send)
        microphone.start()

        task = asyncio.create_task(self.process_sentence_queue())

        while microphone.is_active():
            await asyncio.sleep(0.1)  # adjust the sleep time as needed

        # Wait for the microphone to close
        microphone.finish()

        # Indicate that we've finished
        await dg_connection.finish()

        task.cancel()

async def main():
    client = AsyncGroq(api_key = os.getenv('GROQ_API_KEY'))
    
    """
    # system prompts
    introduction = "As a hotel reservations manager at the Beachview hotel, you are tasked to speak with customers seeking room bookings at the hotel. Your name is Pooja."
    task = "Whenever the user asks for room availability, ask them for the basic details such as dates, number of guests, room preferences, breakfast inclusion, and any special requests - do not ask everything in a single question."
    property_details = "The Beachview hotel is a 5-star property in Mumbai and commands a regal view of the Arabian Sea and the famous Juhu beach. It is located just 30 mins from Mumbai Airport, the travel is also arranged by the concierge. Amenities include Swimming pool, Gym, Spa, Beachfront, Cafe, Business Lounge, Banquet hall, Garden, etc. Prices quoted include breakfast and access to swimming pool, gym, beachfront, garden. Other amenities to be charged as per requirements. If the user asks for prices without breakfast, you can deduct rupees 1000 from the price quoted per night. Also, inclusion of buffet wil cost rupees 1000 extra per person for each lunch and dinner. Room types along with the details is as follows = '1. Superior room = 'area 260 square feet, city view, perfect for business and leisure travellers on the go, priced at rupees 9500 per night, inventory of 150 rooms. 2. Premier room = 'area 260 square feet, ocean view, offering stunning views of the Arabian Sea, priced at rupees 10500 per night, inventory of 100 rooms. 3. Executive room = 'area 350 square feet, city view, large studio rooms, priced at rupees 12500 per night, inventory of 100 rooms. 4. Deluxe room = 'area 350 square feet, ocean view, large studio rooms offering stunning views of the Arabian Sea, priced at rupees 18000 per night, inventory of 50 rooms. 5 = 'Luxury suite = 'area 500 square feet, ocean view, consisting of a living room and a separate bedroom, priced at rupees 25000 per night, inventory of 10 rooms."
    conversation_style = "Communicate concisely and conversationally. Aim for responses in short, clear prose, ideally under 20 words. Always maintain a professional stance."
    language = "Speak like a human as possible, use everyday language and avoid using big and complex words."
    customer_engagement = "Lead the conversation and do not be passive. Most times, engage users by ending with a question. Advise customer on what's best for them."
    transcript_reading = "Don't repeat what's in the transcript. Rephrase if you have to reiterate a point. Use varied sentence structures and vocabulary to ensure each response is unique and personalized."
    ASR_errrors = "This is a real-time transcript, expect there to be errors. If you can guess what the user is trying to say,  then guess and respond. When you must ask for clarification, pretend that you heard the voice and be colloquial while making use of phrases like 'didn't catch that', 'some noise', 'pardon', 'you're coming through choppy', 'static in your speech', 'voice is cutting in and out'. Do not ever mention 'transcription error', and don't repeat yourself."
    role = "If your role cannot do something, try to steer the conversation back to the goal of the conversation and to your role. Don't repeat yourself in doing this. You should still be creative, human-like, and lively."
    brackets = "Answer should not have any parantheses or brackets."

    conversation_history = [
        {"role": "system", "content": introduction},
        {"role": "system", "content": task},
        {"role": "system", "content": property_details},
        {"role": "system", "content": conversation_style},
        {"role": "system", "content": language},
        {"role": "system", "content": customer_engagement},
        {"role": "system", "content": transcript_reading},
        {"role": "system", "content": ASR_errrors},
        {"role": "system", "content": role},
        {"role": "system", "content": brackets},
    ]
    """

    conversation_history = []

    with open('sys_prompts_hotel_eng.txt', 'r') as f:
        for line in f:
            parts = line.strip().split('=')
            if len(parts) == 2:
                key, value = parts
                conversation_history = conversation_history + [
                    {"role": "user", "content": value},
                ]

    intro_query = "Hi, can you please introduce yourself"
    assistant_response = await llm_tts(client, intro_query, conversation_history)
    
    # update conversation history
    conversation_history = conversation_history + [
        {"role": "user", "content": intro_query},
        {"role": "assistant", "content": assistant_response},
    ]

    sentence_queue = Queue()

    ai = ConversationManager(client, conversation_history, sentence_queue)

    try:
        #loop = asyncio.get_event_loop()
        await ai.stt()
    except Exception as e:
        print(f"Could not open socket: {e}")

if __name__ == "__main__":
    if platform.system() == 'Windows':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    # Explicitly setting the default event loop policy for Linux
    #if platform.system() == 'Linux':
    #    asyncio.set_event_loop_policy(asyncio.DefaultEventLoopPolicy())
    asyncio.run(main())
