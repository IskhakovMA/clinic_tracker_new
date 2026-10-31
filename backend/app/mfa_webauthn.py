"""python-fido2 cryptography with explicit RP policy. No clinical payload logging."""
import base64
import json
import logging
from urllib.parse import urlsplit
from fido2.server import Fido2Server
from fido2.webauthn import (AttestedCredentialData, AuthenticationResponse,
    PublicKeyCredentialRpEntity, PublicKeyCredentialUserEntity,
    UserVerificationRequirement)

logging.getLogger('fido2.server').setLevel(logging.ERROR)


def decode(value):
    if not isinstance(value, str) or len(value)>16384:
        raise ValueError('credential_encoding')
    return base64.b64decode(value+'='*((-len(value))%4), altchars=b'-_', validate=True)


def server(rp_id, origin):
    parsed=urlsplit(origin)
    if (parsed.path or parsed.query or parsed.fragment or parsed.username or parsed.password
        or parsed.hostname!=rp_id or parsed.scheme not in {'https','http'}
        or (parsed.scheme=='http' and rp_id!='localhost')):
        raise ValueError('rp_policy')
    return Fido2Server(PublicKeyCredentialRpEntity(id=rp_id,name='Clinic Tracker'),
                      verify_origin=lambda candidate:candidate==origin)


def client_policy(response):
    if not isinstance(response,dict) or not isinstance(response.get('response'),dict):
        raise ValueError('response_structure')
    data=json.loads(decode(response['response']['clientDataJSON']))
    if not isinstance(data,dict):
        raise ValueError('client_data_structure')
    if data.get('crossOrigin',False) is not False or 'topOrigin' in data:
        raise ValueError('cross_origin')
    if decode(response['id'])!=decode(response['rawId']):
        raise ValueError('credential_binding')


def options(rp_id,origin,handle,challenge,credentials,register=False):
    s=server(rp_id,origin)
    if register:
        opts,state=s.register_begin(PublicKeyCredentialUserEntity(id=handle,name='staff',display_name='Staff'),
            credentials=credentials,challenge=challenge,user_verification=UserVerificationRequirement.REQUIRED)
    else:
        opts,state=s.authenticate_begin(credentials,challenge=challenge,
                                       user_verification=UserVerificationRequirement.REQUIRED)
    return dict(opts),json.dumps(state)


def registration(rp_id,origin,state,response):
    client_policy(response)
    data=server(rp_id,origin).register_complete(json.loads(state),response)
    if data.is_backed_up() and not data.is_backup_eligible():raise ValueError('backup_state')
    if data.credential_data.credential_id!=decode(response['id']):raise ValueError('credential_binding')
    return data


def assertion(rp_id,origin,state,response,stored,handle):
    client_policy(response)
    if decode(response['id'])!=stored['credential_id']:raise ValueError('credential_owner')
    actual=response['response'].get('userHandle')
    if actual is not None and decode(actual)!=handle:raise ValueError('user_handle')
    credential=AttestedCredentialData(stored['credential_data'])
    server(rp_id,origin).authenticate_complete(json.loads(state),[credential],response)
    data=AuthenticationResponse.from_dict(response).response.authenticator_data
    if data.is_backed_up() and not data.is_backup_eligible():raise ValueError('backup_state')
    if data.is_backup_eligible()!=bool(stored['be']):raise ValueError('backup_eligibility')
    if (stored['counter'] or data.counter) and data.counter<=stored['counter']:raise ValueError('counter_risk')
    return data
