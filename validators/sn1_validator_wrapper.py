import json
import utils
import torch
import traceback
import bittensor as bt
from prompting.validator import Validator
from prompting.utils.uids import get_random_uids
from prompting.protocol import PromptingSynapse, StreamPromptingSynapse
from prompting.dendrite import DendriteResponseEvent
from .base import QueryValidatorParams, ValidatorAPI
from aiohttp.web_response import Response, StreamResponse
from deprecated import deprecated

class S1ValidatorAPI(ValidatorAPI):
    def __init__(self):
        self.validator = Validator()    
    

    @deprecated(reason="This function is deprecated. Validators use stream synapse now, use get_stream_response instead.")
    async def get_response(self, params:QueryValidatorParams) -> Response:
        try:
            # Guess the task name of current request
            task_name = utils.guess_task_name(params.messages[-1])

            # Get the list of uids to query for this step.
            uids = get_random_uids(self.validator, k=params.k_miners, exclude=params.exclude or []).tolist()
            axons = [self.validator.metagraph.axons[uid] for uid in uids]

            # Make calls to the network with the prompt.
            bt.logging.info(f'Calling dendrite')
            responses = await self.validator.dendrite(
                axons=axons,
                synapse=PromptingSynapse(roles=params.roles, messages=params.messages),
                timeout=params.timeout,
            )

            bt.logging.info(f"Creating DendriteResponseEvent:\n {responses}")
            # Encapsulate the responses in a response event (dataclass)
            response_event = DendriteResponseEvent(responses, torch.LongTensor(uids), params.timeout)

            # convert dict to json
            response = response_event.__state_dict__()

            response['completion_is_valid'] = valid = list(map(utils.completion_is_valid, response['completions']))
            valid_completions = [response['completions'][i] for i, v in enumerate(valid) if v]

            response['task_name'] = task_name
            response['ensemble_result'] = utils.ensemble_result(valid_completions, task_name=task_name, prefer=params.prefer)

            bt.logging.info(f"Response:\n {response}")
            return Response(status=200, reason="I can't believe it's not butter!", text=json.dumps(response))

        except Exception:
            bt.logging.error(f'Encountered in {self.__class__.__name__}:get_response:\n{traceback.format_exc()}')
            return Response(status=500, reason="Internal error")
        
        
    async def get_stream_response(self, params:QueryValidatorParams) -> StreamResponse:
        response = StreamResponse(status=200, reason="OK")
        response.headers['Content-Type'] = 'application/json'

        await response.prepare()  # Prepare and send the headers
        
        try:
            # Guess the task name of current request
            task_name = utils.guess_task_name(params.messages[-1])

            # Get the list of uids to query for this step.
            uids = get_random_uids(self.validator, k=params.k_miners, exclude=params.exclude or []).tolist()
            axons = [self.validator.metagraph.axons[uid] for uid in uids]

            # Make calls to the network with the prompt.
            bt.logging.info(f'Calling dendrite')
            streams_responses = await self.validator.dendrite(
                axons=axons,
                synapse=StreamPromptingSynapse(roles=params.roles, messages=params.messages),
                timeout=params.timeout,
                deserialize=False,
                streaming=True,
            )

            # Asynchronous iteration over streaming responses
            async for stream_result in streams_responses:
                if stream_result is not None:
                    # Convert stream result to JSON and write to the response stream
                    json_data = json.dumps(stream_result)
                    await response.write(json_data.encode('utf-8'))

        except Exception as e:
            bt.logging.error(f'Encountered an error in {self.__class__.__name__}:get_stream_response:\n{traceback.format_exc()}')
            response.set_status(500, reason="Internal error")
            await response.write(json.dumps({'error': str(e)}).encode('utf-8'))
        finally:
            await response.write_eof()  # Ensure to close the response properly

        return response
            

    async def query_validator(self, params:QueryValidatorParams, stream: bool = True) -> Response:        
        if stream:
            return await self.get_stream_response(params)
        else:
            # DEPRECATED
            return await self.get_response(params)
        
