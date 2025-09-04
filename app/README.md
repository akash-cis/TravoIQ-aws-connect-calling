# TravoIQ - Live Call Monitoring Dashboard

## Overview

TravoIQ is a real-time call monitoring and transcript visualization system built with FastAPI and WebSocket technology. It provides live monitoring of customer service calls with real-time transcript updates, call details, and an intuitive web interface for agents and supervisors.

## Features

- **Real-time Call Monitoring**: Live WebSocket-based transcript streaming
- **Call Details Dashboard**: Comprehensive call information display
- **Live Transcript Visualization**: Real-time conversation flow between customers and agents
- **Automatic Call Detection**: Automatic switching to new incoming calls
- **Responsive Web Interface**: Modern, mobile-friendly dashboard design
- **AWS Integration**: Seamless integration with AWS DynamoDB for data storage

## Architecture

### Backend (FastAPI)
- **FastAPI Framework**: High-performance async web framework
- **WebSocket Support**: Real-time bidirectional communication
- **AWS DynamoDB Integration**: Contact details and transcript storage
- **Async Processing**: Non-blocking I/O operations for optimal performance

### Frontend (HTML/JavaScript)
- **Real-time Updates**: Live transcript streaming via WebSocket
- **Responsive Design**: Modern UI with chat-like transcript display
- **Auto-polling**: Automatic detection of new calls
- **Status Management**: Real-time connection and monitoring status

### Data Storage
- **Contact Details Table**: `travoiq-recording-contactDetails`
- **Customer Transcript Table**: `travoiq-recording-contactTranscriptSegments`
- **Agent Transcript Table**: `travoiq-recording-contactTranscriptSegmentsToCustomer`

## Project Structure

```
app/
├── main.py                 # FastAPI application and WebSocket endpoints
├── requirements.txt        # Python dependencies
├── templates/
│   └── index.html         # Web dashboard interface
├── __init__.py            # Python package initialization
└── README.md              # This file
```

## Prerequisites

- Python 3.8+
- AWS Account with DynamoDB access
- AWS credentials configured
- FastAPI and Uvicorn

## Installation

1. **Clone the repository**
   ```bash
   git clone <repository-url>
   cd TravoIQ
   ```

2. **Create and activate virtual environment**
   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   ```

3. **Install dependencies**
   ```bash
   cd app
   pip install -r requirements.txt
   ```

4. **Configure environment variables**
   Create a `.env` file in the root directory:
   ```env
   AWS_REGION_NAME="us-west-2"
   AWS_ACCESS_KEY_ID="your-access-key"
   AWS_SECRET_ACCESS_KEY="your-secret-key"
   KINESIS_STREAM_PREFIX="live-audio-connect-travoiqbot-contact-"
   ```

## Running the Application

1. **Start the FastAPI server**
   ```bash
   uvicorn app.main:app --reload
   ```

2. **Access the dashboard**
   Open your browser and navigate to `http://localhost:8000`

## API Endpoints

### Web Interface
- `GET /` - Main dashboard interface
- `GET /details/{call_id}` - Retrieve call details for a specific call ID
- `GET /latest-call` - Get the most recent call information
- `WS /ws/{call_id}` - WebSocket endpoint for real-time transcript streaming

### WebSocket Events
The WebSocket connection provides real-time transcript updates:
```json
{
  "speaker": "Customer|Agent",
  "text": "Transcript content"
}
```

## Usage

### Monitoring a Call
1. Enter a Call ID in the input field
2. Click "Start Monitoring" to begin real-time monitoring
3. View live transcript updates in the chat interface
4. Call details are automatically displayed above the transcript

### Automatic Call Detection
- The system automatically polls for new calls every 500ms
- When a new call is detected, it automatically switches to monitoring the new call
- No manual intervention required for continuous monitoring

### Transcript Display
- **Customer messages**: Displayed in green bubbles on the left
- **Agent messages**: Displayed in gray bubbles on the right
- Real-time scrolling to the latest messages
- Automatic speaker identification

## Configuration

### AWS DynamoDB Tables
The application expects three DynamoDB tables:
- **Contact Details**: Stores call metadata (contactId, customerPhoneNumber, callTimestamp)
- **Customer Transcript**: Stores customer speech segments with ContactId as partition key
- **Agent Transcript**: Stores agent speech segments with ContactId as partition key

### Environment Variables
- `AWS_REGION_NAME`: AWS region for DynamoDB access
- `AWS_ACCESS_KEY_ID`: AWS access key for authentication
- `AWS_SECRET_ACCESS_KEY`: AWS secret key for authentication
- `KINESIS_STREAM_PREFIX`: Prefix for Kinesis stream names

## Dependencies

### Core Dependencies
- **FastAPI**: Modern web framework for building APIs
- **Uvicorn**: ASGI server for running FastAPI applications
- **Jinja2**: Template engine for HTML rendering
- **Boto3**: AWS SDK for Python

### WebSocket & Async
- **WebSockets**: WebSocket protocol support
- **Asyncio**: Asynchronous I/O support

### Audio Processing (Optional)
- **Pydub**: Audio file manipulation
- **EBMLite**: MKV file parsing support

## Development

### Code Structure
- **Main Application**: `main.py` contains all FastAPI routes and WebSocket logic
- **Templates**: HTML templates for the web interface
- **Configuration**: Environment-based configuration management

### Key Classes
- **DecimalEncoder**: Custom JSON encoder for DynamoDB Decimal types
- **WebSocket Management**: Async WebSocket handling with automatic reconnection

### Error Handling
- Comprehensive logging with structured error messages
- Graceful WebSocket disconnection handling
- AWS error handling with appropriate HTTP status codes

## Security Considerations

- AWS credentials should be stored securely (not in version control)
- Consider using AWS IAM roles for production deployments
- Implement proper authentication for production use
- Validate and sanitize all input parameters

## Performance Features

- **Async Processing**: Non-blocking I/O operations
- **Connection Pooling**: Efficient AWS resource management
- **Real-time Updates**: Minimal latency for transcript streaming
- **Automatic Cleanup**: Proper resource management and cleanup

## Troubleshooting

### Common Issues
1. **WebSocket Connection Failed**: Check if the server is running and accessible
2. **No Transcript Updates**: Verify the Call ID exists in DynamoDB tables
3. **AWS Connection Errors**: Ensure AWS credentials are properly configured
4. **Port Already in Use**: Change the port number in the uvicorn command

### Logging
The application provides comprehensive logging:
- Connection events
- WebSocket message handling
- AWS operation results
- Error details with stack traces

## Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Test thoroughly
5. Submit a pull request

## License

[Add your license information here]

## Support

For support and questions, please contact the development team or create an issue in the repository.
