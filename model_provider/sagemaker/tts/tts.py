import concurrent.futures
from typing import IO, Optional, Any
from enum import Enum

from core.model_runtime.entities.common_entities import I18nObject
from core.model_runtime.entities.model_entities import AIModelEntity, FetchFrom, ModelType
from core.model_runtime.errors.invoke import (
    InvokeAuthorizationError,
    InvokeBadRequestError,
    InvokeConnectionError,
    InvokeError,
    InvokeRateLimitError,
    InvokeServerUnavailableError,
)
from core.model_runtime.errors.validate import CredentialsValidateFailedError
from core.model_runtime.model_providers.__base.tts_model import TTSModel
import requests

class TTSModelType(Enum):
    PresetVoice = "PresetVoice"
    CloneVoice = "CloneVoice"
    CloneVoice_CrossLingual = "CloneVoice_CrossLingual"
    InstructVoice = "InstructVoice"

class SageMakerText2SpeechModel(TTSModel):

    sagemaker_client: Any = None
    s3_client : Any = None

    def validate_credentials(self, model: str, credentials: dict) -> None:
        """
                Validate model credentials

                :param model: model name
                :param credentials: model credentials
                :return:
                """
        pass

    def _build_tts_payload(self, content_text:str, model_type:str, model_role:str, prompt_text:str, prompt_audio:str, lang_tag:str, instruct_text:str):
        if model_type == TTSModelType.PresetVoice.value and model_role:
            return { "tts_text" : content_text, "role" : model_role }
        if model_type == TTSModelType.CloneVoice.value and prompt_text and prompt_audio:
            return { "tts_text" : content_text, "prompt_text": prompt_text, "prompt_audio" : prompt_audio }
        if model_type ==  TTSModelType.CloneVoice_CrossLingual.value and prompt_audio and lang_tag:
            return { "tts_text" : content_text, "prompt_audio" : prompt_audio, "lang_tag" : lang_tag }
        if model_type ==  TTSModelType.InstructVoice.value and instruct_text and model_role:
            return { "tts_text" : content_text, "role" : model_role, "instruct_text" : instruct_text }

        raise RuntimeError(f"Invalid params for {model_type}")

    def _invoke(self, model: str, tenant_id: str, credentials: dict, content_text: str, voice: str,
                user: Optional[str] = None):
        """
        _invoke text2speech model

        :param model: model name
        :param tenant_id: user tenant id
        :param credentials: model credentials
        :param voice: model timbre
        :param content_text: text content to be translated
        :param user: unique user id
        :return: text translated to audio file
        """
        logger.warning(f'model: {model}.')
        logger.warning(f'tenant_id: {tenant_id}.')
        logger.warning(f'content_text: {content_text}.')
        logger.warning(f'voice: {voice}.')
        logger.warning(f'user: {user}.')

        if not self.sagemaker_client:
            access_key = credentials.get('aws_access_key_id')
            secret_key = credentials.get('aws_secret_access_key')
            aws_region = credentials.get('aws_region')
            if aws_region:
                if access_key and secret_key:
                    self.sagemaker_client = boto3.client("sagemaker-runtime", 
                        aws_access_key_id=access_key,
                        aws_secret_access_key=secret_key,
                        region_name=aws_region)
                    self.s3_client = boto3.client("s3",
                        aws_access_key_id=access_key,
                        aws_secret_access_key=secret_key,
                        region_name=aws_region)
                else:
                    self.sagemaker_client = boto3.client("sagemaker-runtime", region_name=aws_region)
                    self.s3_client = boto3.client("s3", region_name=aws_region)
            else:
                self.sagemaker_client = boto3.client("sagemaker-runtime")
                self.s3_client = boto3.client("s3")

        model_type = credentials.get('model_type', 'PresetVoice')
        model_role = credentials.get('model_role')
        prompt_text = credentials.get('prompt_text')
        prompt_audio = credentials.get('prompt_audio')
        instruct_text = credentials.get('instruct_text')
        lang_tag = credentials.get('lang_tag')
        sagemaker_endpoint = credentials.get('sagemaker_endpoint')
        payload = self._build_tts_payload(
            content_text, 
            model_type, 
            model_role, 
            prompt_text, 
            prompt_audio, 
            lang_tag, 
            instruct_text
        )

        return self._tts_invoke_streaming(model, credentials, content_text, voice)

    def get_customizable_model_schema(self, model: str, credentials: dict) -> AIModelEntity | None:
        """
            used to define customizable model schema
        """
        entity = AIModelEntity(
            model=model,
            label=I18nObject(
                en_US=model
            ),
            fetch_from=FetchFrom.CUSTOMIZABLE_MODEL,
            model_type=ModelType.TTS,
            model_properties={},
            parameter_rules=[]
        )

        return entity

    @property
    def _invoke_error_mapping(self) -> dict[type[InvokeError], list[type[Exception]]]:
        """
        Map model invoke error to unified error
        The key is the error type thrown to the caller
        The value is the error type thrown by the model,
        which needs to be converted into a unified error type for the caller.

        :return: Invoke error mapping
        """
        return {
            InvokeConnectionError: [
                InvokeConnectionError
            ],
            InvokeServerUnavailableError: [
                InvokeServerUnavailableError
            ],
            InvokeRateLimitError: [
                InvokeRateLimitError
            ],
            InvokeAuthorizationError: [
                InvokeAuthorizationError
            ],
            InvokeBadRequestError: [
                InvokeBadRequestError,
                KeyError,
                ValueError
            ]
        }

    def _get_model_default_voice(self, model: str, credentials: dict) -> any:
        return ""

    def _get_model_word_limit(self, model: str, credentials: dict) -> int:
        return 600

    def _get_model_audio_type(self, model: str, credentials: dict) -> str:
        return "mp3"

    def _get_model_workers_limit(self, model: str, credentials: dict) -> int:
        return 5

    def _invoke_sagemaker(self, payload:dict, endpoint:str):
        response_model = self.sagemaker_client.invoke_endpoint(
            EndpointName=endpoint,
            Body=json.dumps(payload),
            ContentType="application/json",
        )
        json_str = response_model['Body'].read().decode('utf8')
        json_obj = json.loads(json_str)
        return json_obj

    def _tts_invoke_streaming(self, payload:dict, sagemaker_endpoint:str) -> any:
        """
        _tts_invoke_streaming text2speech model

        :param model: model name
        :param credentials: model credentials
        :param content_text: text content to be translated
        :param voice: model timbre
        :return: text translated to audio file
        """
        try:
            word_limit = self._get_model_word_limit(model, credentials)
            if len(content_text) > word_limit:
                sentences = self._split_text_into_sentences(payload.get("content_text"), max_length=word_limit)
                len_sent = len(sentences)
                executor = concurrent.futures.ThreadPoolExecutor(max_workers=min(4, len_sent))
                payloads = [payload] * len_sent
                for idx in range(len_sent):
                    payloads[idx]["tts_text"] = sentences[idx]

                futures = [ executor.submit(
                    self._invoke_sagemaker,
                    payload=payload,
                    endpoint=sagemaker_endpoint,
                )
                    for payload in payloads]

                for index, future in enumerate(futures):
                    resp = future.result()
                    logger.warning(f"resp: {resp}")
                    audio_bytes = requests.get(resp.get('s3_presign_url')).content
                    for i in range(0, len(audio_bytes), 1024):
                        yield audio_bytes[i:i + 1024]
            else:
                resp = self._invoke_sagemaker(payload, sagemaker_endpoint)
                logger.warning(f"resp: {resp}")
                audio_bytes = requests.get(resp.get('s3_presign_url')).content

                for i in range(0, len(audio_bytes), 1024):
                    yield audio_bytes[i:i + 1024]
        except Exception as ex:
            raise InvokeBadRequestError(str(ex))
