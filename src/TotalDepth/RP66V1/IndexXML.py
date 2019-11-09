import datetime
import io
import logging
import multiprocessing
import os
import sys
import time
import typing

from TotalDepth.RP66V1 import ExceptionTotalDepthRP66V1
from TotalDepth.RP66V1.core import File, LogPass
from TotalDepth.RP66V1.core import LogicalFile
from TotalDepth.RP66V1.core import LogicalRecord
from TotalDepth.RP66V1.core import RepCode
from TotalDepth.RP66V1.core import StorageUnitLabel
from TotalDepth.RP66V1.core.Index import ExceptionIndex
from TotalDepth.RP66V1.core.XAxis import IFLRReference
from TotalDepth.common import process
from TotalDepth.common import cmn_cmd_opts
from TotalDepth.common import Rle
from TotalDepth.common import xml
from TotalDepth.util import DirWalk
from TotalDepth.util.bin_file_type import binary_file_type_from_path
from TotalDepth.util import gnuplot
from TotalDepth.util import XmlWrite

__author__  = 'Paul Ross'
__date__    = '2019-04-10'
__version__ = '0.1.0'
__rights__  = 'Copyright (c) 2019 Paul Ross. All rights reserved.'


class ExceptionRP66V1IndexXMLRead(ExceptionIndex):
    pass

class ExceptionIndexXML(ExceptionTotalDepthRP66V1):
    pass


class ExceptionIndexXMLRead(ExceptionIndexXML):
    pass


class ExceptionLogPassXML(LogPass.ExceptionLogPass):
    pass


logger = logging.getLogger(__file__)

XML_SCHEMA_VERSION = '0.1.0'
XML_TIMESTAMP_FORMAT_NO_TZ = '%Y-%m-%d %H:%M:%S.%f'

# UTC with a TZ
# datetime.datetime.utcnow().replace(tzinfo=datetime.timezone(datetime.timedelta(0))).strftime('%Y-%m-%d %H:%M:%S.%f%z')
# '2019-05-14 17:33:01.147341+0000'


def xml_single_element(element: xml.etree.Element, xpath: str) -> xml.etree.Element:
    """Selects a single XML element in the Xpath."""
    elems = list(element.iterfind(xpath))
    if len(elems) != 1:
        raise ExceptionIndexXMLRead(f'Expected single element at Xpath {xpath} but found {len(elems)}')
    return elems[0]


def xml_rle_write(rle: Rle.RLE, element_name: str, xml_stream: XmlWrite.XmlStream, hex_output: bool) -> None:
    with XmlWrite.Element(xml_stream, element_name, {'count': f'{rle.num_values():d}', 'rle_len': f'{len(rle):d}',}):
        for rle_item in rle.rle_items:
            attrs = {
                'datum': f'0x{rle_item.datum:x}' if hex_output else f'{rle_item.datum}',
                'stride': f'0x{rle_item.stride:x}' if hex_output else f'{rle_item.stride}',
                'repeat': f'{rle_item.repeat:d}',
            }
            with XmlWrite.Element(xml_stream, 'RLE', attrs):
                pass


def xml_integer_attribute_read(element: xml.etree.Element, attr: str) -> int:
    attribute = element.attrib[attr]
    if attribute.startswith('0x'):
        return int(attribute, 16)
    return int(attribute)


def xml_rle_read(element: xml.etree.Element) -> Rle.RLE:
    """Read the RLE values under an element and return the RLE object.

    Example:

    .. code-block:: xml

        <VisibleRecords count="237" rle_len="56">
            <RLE datum="0x50" repeat="6" stride="0x2000"/>
            <RLE datum="0xe048" repeat="3" stride="0x2000"/>
            <RLE datum="0x16044" repeat="3" stride="0x2000"/>
        </VisibleRecords>

    May raise an ExceptionIndexXMLRead or other exceptions.
    """
    def _rle_convert_datum_or_stride(attr: str) -> typing.Union[int, float]:
        if attr.startswith('0x'):
            return int(attr, 16)
        if '.' in attr:
            return float(attr)
        return int(attr)

    ret = Rle.RLE()
    # print('TRACE:', element, element.attrib)
    for element_rle in element.iterfind('./RLE'):
        rle_item = Rle.RLEItem(_rle_convert_datum_or_stride(element_rle.attrib['datum']))
        rle_item.repeat = _rle_convert_datum_or_stride(element_rle.attrib['repeat'])
        rle_item.stride = _rle_convert_datum_or_stride(element_rle.attrib['stride'])
        ret.rle_items.append(rle_item)
    # Sanity check on element.attrib['count'] and element.attrib['rle_len']
    count: int = int(element.attrib['count'])
    if count != ret.num_values():
        raise ExceptionIndexXMLRead(f'Expected {count} RLE items but got {ret.num_values()}')
    rle_len: int = int(element.attrib['rle_len'])
    if rle_len != len(ret):
        raise ExceptionIndexXMLRead(f'Expected {rle_len} RLE items but got {len(ret)}')
    return ret


def xml_object_name_attributes(object_name: RepCode.ObjectName) -> typing.Dict[str, str]:
    return {
        'O': f'{object_name.O}',
        'C': f'{object_name.C}',
        'I': f'{object_name.I.decode("ascii")}',
    }


def xml_object_name(node: xml.etree.Element) -> RepCode.ObjectName:
    return RepCode.ObjectName(node.attrib['O'], node.attrib['C'], node.attrib['I'].encode('ascii'))


def xml_write_value(xml_stream: XmlWrite.XmlStream, value: typing.Any) -> None:
    """Write a value to the XML stream with specific type as an attribute.
    This writes either a <Value> or an <ObjectName> element."""
    if isinstance(value, RepCode.ObjectName):
        with XmlWrite.Element(xml_stream, 'ObjectName', xml_object_name_attributes(value)):
            pass
    else:
        if isinstance(value, bytes):
            typ = 'bytes'
            # print('TRACE: xml_write_value()', value)
            _value = value.decode('latin-1')#, errors='ignore')
        elif isinstance(value, float):
            typ = 'float'
            _value = str(value)
        elif isinstance(value, int):
            typ = 'int'
            _value = str(value)
        elif isinstance(value, RepCode.DateTime):
            typ = 'TotalDepth.RP66V1.core.RepCode.DateTime'
            _value = str(value)
        elif isinstance(value, str):
            typ = 'str'
            _value = value
        else:
            typ = 'unknown'
            _value = str(value)
        with XmlWrite.Element(xml_stream, 'Value', {'type': typ, 'value': _value}):
            # xml_stream.characters(_value)
            pass


def frame_channel_to_XML(channel: LogPass.FrameChannel, xml_stream: XmlWrite.XmlStream) -> None:
    """Writes a XML Channel node suitable for RP66V1.

    Example:

    .. code-block:: xml

        <Channel C="0" I="DEPTH" O="35" count="1" dimensions="1" long_name="Depth Channel" rep_code="7" units="m"/>
    """
    channel_attrs = {
        'O': f'{channel.ident.O}',
        'C': f'{channel.ident.C}',
        'I': f'{channel.ident.I.decode("ascii")}',
        'long_name': f'{channel.long_name.decode("ascii")}',
        'rep_code': f'{channel.rep_code:d}',
        'units': f'{channel.units.decode("ascii")}',
        'dimensions': ','.join(f'{v:d}' for v in channel.dimensions),
        'count': f'{channel.count:d}',
    }
    with XmlWrite.Element(xml_stream, 'Channel', channel_attrs):
        pass


def frame_channel_from_XML(channel_node: xml.etree.Element) -> LogPass.FrameChannel:
    """Initialise with a XML Channel node.

    Example:

    .. code-block:: xml

        <Channel C="0" I="DEPTH" O="35" count="1" dimensions="1" long_name="Depth Channel" rep_code="7" units="m"/>
    """
    if channel_node.tag != 'Channel':
        raise ValueError(f'Got element tag of "{channel_node.tag}" but expected "Channel"')
    return LogPass.FrameChannel(
        ident=channel_node.attrib['I'].encode('ascii'),
        long_name=channel_node.attrib['long_name'].encode('ascii'),
        rep_code=int(channel_node.attrib['rep_code']),
        units=channel_node.attrib['units'].encode('ascii'),
        dimensions=[int(v) for v in channel_node.attrib['dimensions'].split(',')],
        function_np_dtype=RepCode.numpy_dtype
    )


def frame_array_to_XML(frame_array: LogPass.FrameArray,
                       iflr_data: typing.Sequence[IFLRReference],
                       xml_stream: XmlWrite.XmlStream) -> None:
    """Writes a XML FrameArray node suitable for RP66V1.

    Example:

    .. code-block:: xml

        <FrameArray C="0" I="0B" O="11" description="">
          <Channels channel_count="9">
            <Channel C="0" I="DEPT" O="11" count="1" dimensions="1" long_name="MWD Tool Measurement Depth" rep_code="2" units="0.1 in"/>
            <Channel C="0" I="INC" O="11" count="1" dimensions="1" long_name="Inclination" rep_code="2" units="deg"/>
            <Channel C="0" I="AZI" O="11" count="1" dimensions="1" long_name="Azimuth" rep_code="2" units="deg"/>
            ...
          </Channels>
          <IFLR count="83">
              <FrameNumbers count="83" rle_len="1">
                <RLE datum="1" repeat="82" stride="1"/>
              </FrameNumbers>
              <LRSH count="83" rle_len="2">
                <RLE datum="0x14ac" repeat="61" stride="0x30"/>
                <RLE datum="0x2050" repeat="20" stride="0x30"/>
              </LRSH>
              <Xaxis count="83" rle_len="42">
                <RLE datum="0.0" repeat="1" stride="75197.0"/>
                <RLE datum="154724.0" repeat="1" stride="79882.0"/>
              </Xaxis>
          </IFLR>
        </FrameArray>
    """
    frame_array_attrs = {
        'O': f'{frame_array.ident.O}',
        'C': f'{frame_array.ident.C}',
        'I': f'{frame_array.ident.I.decode("ascii")}',
        'description': frame_array.description.decode('ascii'),
        'x_axis' : frame_array.channels[0].ident.I.decode("ascii"),
        'x_units' : frame_array.channels[0].units.decode("ascii"),
    }

    with XmlWrite.Element(xml_stream, 'FrameArray', frame_array_attrs):
        with XmlWrite.Element(xml_stream, 'Channels', {'count': f'{len(frame_array)}'}):
            for channel in frame_array.channels:
                    frame_channel_to_XML(channel, xml_stream)
        with XmlWrite.Element(xml_stream, 'IFLR', {'count' : f'{len(iflr_data)}'}):
            # Frame number output
            rle = Rle.create_rle(v.frame_number for v in iflr_data)
            xml_rle_write(rle, 'FrameNumbers', xml_stream, hex_output=False)
            # IFLR file position
            rle = Rle.create_rle(v.logical_record_position.lrsh_position for v in iflr_data)
            xml_rle_write(rle, 'LRSH', xml_stream, hex_output=True)
            # Xaxis output
            rle = Rle.create_rle(v.x_axis for v in iflr_data)
            xml_rle_write(rle, 'Xaxis', xml_stream, hex_output=False)


def iflr_data_from_xml(frame_array_node: xml.etree.Element) -> typing.Iterator[IFLRReference]:
    """Returns a sequence of IFLRReference objects from XML."""
    iflr_node = xml_single_element(frame_array_node, './IFLR')
    rle_lrsh = xml_rle_read(xml_single_element(iflr_node, './LRSH'))
    rle_frames = xml_rle_read(xml_single_element(iflr_node, './FrameNumbers'))
    rle_xaxis = xml_rle_read(xml_single_element(iflr_node, './Xaxis'))
    if len({int(iflr_node.attrib['count']), rle_lrsh.num_values(), rle_frames.num_values(), rle_xaxis.num_values()}) != 1:
        raise LogPass.ExceptionFrameArrayInit('Mismatched counts of LRSH, FrameNumbers and Xaxis')
    return (IFLRReference(*v) for v in zip(rle_lrsh.values(), rle_frames.values(), rle_xaxis.values()))


def frame_array_from_XML(frame_array_node: xml.etree.Element) \
        -> typing.Tuple[LogPass.FrameArray, typing.Iterator[IFLRReference]]:
    """Initialise a FrameArray from a XML Channel node. For an example of the XML see frame_array_to_XML."""
    if frame_array_node.tag != 'FrameArray':
        raise ValueError(f'Got element tag of "{frame_array_node.tag}" but expected "FrameArray"')
    ret = LogPass.FrameArray(
        ident=xml_object_name(frame_array_node),
        description=frame_array_node.attrib['description'].encode('ascii')
    )
    # TODO: Check Channel count
    for channnel_node in frame_array_node.iterfind('./Channels/Channel'):
        ret.append(frame_channel_from_XML(channnel_node))
    return ret, iflr_data_from_xml(frame_array_node)


def log_pass_to_XML(log_pass: LogPass.LogPass,
                    iflr_data_map: typing.Dict[typing.Hashable, typing.Sequence[IFLRReference]],
                    xml_stream: XmlWrite.XmlStream) -> None:
    """Writes a XML LogPass node suitable for RP66V1. Example:

    .. code-block:: xml

        <LogPass count="4">
            <FrameArray C="0" I="600T" O="44" description="">
                ...
            <FrameArray>
            ...
        </LogPass>
    """
    with XmlWrite.Element(xml_stream, 'LogPass', {'count': f'{len(log_pass)}'}):
        for frame_array in log_pass.frame_arrays:
            if frame_array.ident not in iflr_data_map:
                raise ExceptionLogPassXML(f'Missing ident {frame_array.ident} in keys {list(iflr_data_map.keys())}')
            frame_array_to_XML(frame_array, iflr_data_map[frame_array.ident], xml_stream)


def log_pass_from_XML(log_pass_node: xml.etree.Element) \
        -> typing.Tuple[LogPass.LogPass, typing.Dict[typing.Hashable, typing.Iterator[typing.Tuple[int, int, typing.Any]]]]:
    log_pass = LogPass.LogPass()
    iflr_map = {}
    for frame_array_node in log_pass_node.iterfind('./FrameArray'):
        frame_array, iflr_data = frame_array_from_XML(frame_array_node)
        iflr_map[frame_array.ident] = iflr_data
        log_pass.append(frame_array)
    return log_pass, iflr_map


def _write_xml_eflr_object(obj: LogicalRecord.EFLR.Object, xml_stream: XmlWrite.XmlStream) -> None:
    with XmlWrite.Element(xml_stream, 'Object', xml_object_name_attributes(obj.name)):
        for attr in obj.attrs:
            attr_atributes = {
                'label': attr.label.decode('ascii'),
                'count': f'{attr.count:d}',
                'rc': f'{attr.rep_code:d}',
                # TODO: Remove this as duplicate?
                'rc_ascii': f'{RepCode.REP_CODE_INT_TO_STR[attr.rep_code]}',
                'units': attr.units.decode('ascii'),
            }
            # with XmlWrite.Element(xml_stream, 'Attribute', attr_atributes):
            #     if attr.value is not None:
            #         with XmlWrite.Element(xml_stream, 'Values', {'count': f'{len(attr.value)}'}):
            #             for v in attr.value:
            #                 LogPassXML.xml_write_value(xml_stream, v)
            #     else:
            #         with XmlWrite.Element(xml_stream, 'Values', {'count': '0'}):
            #             pass
            with XmlWrite.Element(xml_stream, 'Attribute', attr_atributes):
                if attr.value is not None:
                    for v in attr.value:
                        xml_write_value(xml_stream, v)
                # else:
                #     with XmlWrite.Element(xml_stream, 'Values', {'count': '0'}):
                #         pass


def write_logical_file_to_xml(logical_file_index: int, logical_file: LogicalFile, xml_stream: XmlWrite.XmlStream, private: bool) -> None:
    with XmlWrite.Element(xml_stream, 'LogicalFile', {
        'has_log_pass': str(logical_file.log_pass is not None),
        'index': f'{logical_file_index:d}',
        # 'schema_version': XML_SCHEMA_VERSION,
    }):
        for position, eflr in logical_file.eflrs:
            attrs = {
                'vr_position': f'0x{position.vr_position:x}',
                'lrsh_position': f'0x{position.lrsh_position:x}',
                'lr_type': f'{eflr.lr_type:d}',
                'set_type': f'{eflr.set.type.decode("ascii")}',
                'set_name': f'{eflr.set.name.decode("ascii")}',
                'object_count': f'{len(eflr.objects):d}'
            }
            with XmlWrite.Element(xml_stream, 'EFLR', attrs):
                if private or LogicalRecord.Types.is_public(eflr.lr_type):
                    for obj in eflr.objects:
                        _write_xml_eflr_object(obj, xml_stream)
        if logical_file.log_pass is not None:
            log_pass_to_XML(logical_file.log_pass, logical_file.iflr_position_map, xml_stream)


def write_logical_file_sequence_to_xml(logical_file_sequence: LogicalFile.LogicalIndex,
                                       output_stream: typing.TextIO, private: bool) -> None:
    """Takes a LogicalIndex and writes the index to an XML stream."""
    with XmlWrite.XmlStream(output_stream) as xml_stream:
        with XmlWrite.Element(xml_stream, 'RP66V1FileIndex', {
            'path': logical_file_sequence.id,
            'size': f'{os.path.getsize(logical_file_sequence.id):d}',
            'schema_version': XML_SCHEMA_VERSION,
            'utc_file_mtime': str(datetime.datetime.utcfromtimestamp(os.stat(logical_file_sequence.id).st_mtime)),
            'utc_now': str(datetime.datetime.utcnow()),
            'creator': f'{__name__}',
        }):
            with XmlWrite.Element(
                    xml_stream, 'StorageUnitLabel',
                    {
                        'sequence_number': str(logical_file_sequence.storage_unit_label.storage_unit_sequence_number),
                        'dlis_version': logical_file_sequence.storage_unit_label.dlis_version.decode('ascii'),
                        'storage_unit_structure': logical_file_sequence.storage_unit_label.storage_unit_structure.decode('ascii'),
                        'maximum_record_length': str(logical_file_sequence.storage_unit_label.maximum_record_length),
                        'storage_set_identifier': logical_file_sequence.storage_unit_label.storage_set_identifier.decode('ascii'),
                    }):
                pass
            with XmlWrite.Element(xml_stream, 'LogicalFiles', {'count': f'{len(logical_file_sequence.logical_files):d}'}):
                for lf, logical_file in enumerate(logical_file_sequence.logical_files):
                    write_logical_file_to_xml(lf, logical_file, xml_stream, private)
            # Visible records at the end
            rle_visible_records = Rle.create_rle(logical_file_sequence.visible_record_positions)
            xml_rle_write(rle_visible_records, 'VisibleRecords', xml_stream, hex_output=True)


def read_logical_file_from_xml(logical_file_node: xml.etree.Element,
                               rp66v1_file: File.FileRead) -> LogicalFile.LogicalFile:
    """Creates a LogicalFile instance from the XML index.

    XML is as follows::

        <LogicalFile has_log_pass="True" schema_version="0.1.0">
          <EFLR lr_type="0" lrsh_position="0x54" object_count="1" set_name="" set_type="FILE-HEADER" vr_position="0x50">
            ...
          </EFLR>
          <EFLR lr_type="1" lrsh_position="0xd0" object_count="1" set_name="" set_type="ORIGIN" vr_position="0x50">
            ...
          </EFLR>
          <!-- More EFLRs -->
          <LogPass>
            <FrameArray C="0" I="1200000T" O="44" description="DOMAIN_TIME">
              <Channels count="4">
                <Channel C="3" I="TIME" O="44" count="1" dimensions="1" long_name="Time Index" rep_code="2" units="ms"/>
                ...
              </Channels>
              <IFLR count="1">
                <FrameNumbers count="1" rle_len="1">
                  <RLE datum="1" repeat="0" stride="0"/>
                </FrameNumbers>
                <LRSH count="1" rle_len="1">
                  <RLE datum="0x1b5dd4" repeat="0" stride="0x0"/>
                </LRSH>
                <Xaxis count="1" rle_len="1">
                  <RLE datum="260419.0" repeat="0" stride="0"/>
                </Xaxis>
              </IFLR>
            </FrameArray>
          </LogPass>
        </LogicalFile>

    We cheat a bit here are read the EFLR from the original file rather than the XML, less efficient but less code.
    This also means the LogPass is created from EFLRs rather than the index using LogPassXML.log_pass_from_XML().
    This could be obviated by creating EFLRs from the index, however this would mean the index needs all of EFLR (or
    do some lazy evaluation).

    We take the IFLR data from the XML index however.
    """
    # FIXME: This should not have to go to the original file. This is reading EFLRs from the file rather than the index.
    assert logical_file_node.tag == 'LogicalFile'
    eflr_nodes: typing.List[xml.etree.Element] = list(logical_file_node.iterfind('./EFLR'))
    # Error checking the EFLRs are sensible.
    if len(eflr_nodes) < 2:
        raise ExceptionRP66V1IndexXMLRead(
            'Not enough EFLRs to create a LogicalFile,'
            ' need at least FILE-HEADER and ORIGIN [RP66V1 2.2.3 Logical File (LF)]'
        )
    if eflr_nodes[0].attrib['set_type'] != 'FILE-HEADER':
        raise ExceptionRP66V1IndexXMLRead(
            'First EFLR in a Logical File must be a FILE-HEADER [RP66V1 2.2.3 Logical File (LF)]'
        )
    if eflr_nodes[1].attrib['set_type'] != 'ORIGIN':
        raise ExceptionRP66V1IndexXMLRead(
            'Second EFLR in a Logical File must be a ORIGIN [RP66V1 2.2.3 Logical File (LF)]'
        )
    fld = rp66v1_file.get_file_logical_data(
        xml_integer_attribute_read(eflr_nodes[0], 'vr_position'),
        xml_integer_attribute_read(eflr_nodes[0], 'lrsh_position'),
    )
    logical_file = LogicalFile.LogicalFile(fld, LogicalRecord.EFLR.ExplicitlyFormattedLogicalRecord(fld.lr_type, fld.logical_data))
    for eflr_node in eflr_nodes[1:]:
        fld = rp66v1_file.get_file_logical_data(
            xml_integer_attribute_read(eflr_node, 'vr_position'),
            xml_integer_attribute_read(eflr_node, 'lrsh_position'),
        )
        eflr = LogicalRecord.EFLR.ExplicitlyFormattedLogicalRecord(fld.lr_type, fld.logical_data)
        logical_file.add_eflr(fld, eflr)
    for frame_array_node in logical_file_node.iterfind('./LogPass/FrameArray'):
        frame_array_object_name = xml_object_name(frame_array_node)
        iflr_data = iflr_data_from_xml(frame_array_node)
        if frame_array_object_name in logical_file.iflr_position_map:
            raise ExceptionRP66V1IndexXMLRead(f'Duplicate Frame Array entry {frame_array_object_name}')
        logical_file.iflr_position_map[frame_array_object_name] = list(iflr_data)
    return logical_file


def read_storage_unit_label_from_xml(root: xml.etree.Element) -> StorageUnitLabel.StorageUnitLabel:
    # Read the StorageUnitLabel element. Example::
    #
    # <StorageUnitLabel dlis_version="V1.00" maximum_record_length="8192" sequence_number="1"
    #     storage_set_identifier="Default Storage Set                                         "
    #     storage_unit_structure="RECORD"/>
    sul_element = xml_single_element(root, './StorageUnitLabel')
    dlis_version = bytes(sul_element.attrib['dlis_version'], 'ascii')
    exp = b'V1.00'
    if dlis_version != exp:
        raise ExceptionIndexXMLRead(f'Found DLIS version {dlis_version} but expected {exp}')
    sequence_number = int(sul_element.attrib['sequence_number'])
    if sequence_number <= 0:
        # Reference [RP66V1 2.3.2 Storage Unit Label (SUL), Comment 1]
        raise ExceptionIndexXMLRead(f'Sequence number must be >0 not {sequence_number}')
    maximum_record_length = int(sul_element.attrib['maximum_record_length'])
    storage_unit_structure = bytes(sul_element.attrib['storage_unit_structure'], 'ascii')
    exp = b'RECORD'
    if storage_unit_structure != exp:
        raise ExceptionIndexXMLRead(
            f'Found Storage Unit Structure {storage_unit_structure} but expected {exp}'
        )
    storage_set_identifier = bytes(sul_element.attrib['storage_set_identifier'], 'ascii')
    # Assemble the bytes for the StorageUnitLable
    ret = StorageUnitLabel.create_storage_unit_label(
        sequence_number,
        dlis_version,
        maximum_record_length,
        storage_set_identifier
    )
    return ret


def read_logical_index_from_xml(index_path: str, archive_root: str) -> LogicalFile.LogicalIndex:
    # FIXME: This should not have to go to the original file.
    # self.index_path = index_path
    # self.archive_root = archive_root
    # TODO: Is binary required for XML?
    with open(index_path, 'rb') as fobj:
        root: xml.etree.Element = xml.etree.parse(fobj).getroot()
    if root.tag != 'RP66V1FileIndex':
        raise ExceptionRP66V1IndexXMLRead(f'Got element tag of "{root.tag}" but expected "RP66V1FileIndex"')
    # Read the root element RP66V1FileIndex. Example::
    #
    # <RP66V1FileIndex path="tmp/data_unpack/AUS/2010-2015/W004274/Yulleroo_4_Log_Data_A/LWD/Y4_GR_RES_RM.dlis"
    #     schema_version="0.1.0"
    #     size="1937848"
    #     utc_file_mtime="2019-03-18 16:07:28"
    #     utc_now="2019-04-27 10:24:13.982071">
    if root.attrib['schema_version'] != XML_SCHEMA_VERSION:
        raise ExceptionRP66V1IndexXMLRead(
            f'Found schema version {root.attrib["schema_version"]} but expected {XML_SCHEMA_VERSION}'
        )
    path = root.attrib['path']

    original_file_path: str = os.path.join(archive_root, path)
    if not os.path.isfile(original_file_path):
        raise ExceptionRP66V1IndexXMLRead(f'Not a file: "{original_file_path}"')
    bin_file_type = binary_file_type_from_path(original_file_path)
    if bin_file_type != 'RP66V1':
        raise ExceptionRP66V1IndexXMLRead(
            f'File: "{original_file_path}" is not a RP66V1 file but "{bin_file_type}"')

    # size = int(root.attrib['size'])
    # utc_file_mtime = datetime.datetime.strptime(
    #     root.attrib['utc_file_mtime'], XML_TIMESTAMP_FORMAT_NO_TZ,
    # )
    # utc_now = datetime.datetime.strptime(
    #     root.attrib['utc_now'], XML_TIMESTAMP_FORMAT_NO_TZ,
    # )
    logical_index = LogicalFile.LogicalIndex(None, original_file_path)
    logical_index.storage_unit_label = read_storage_unit_label_from_xml(root)
    # Logical Files
    logical_files_node = xml_single_element(root, './LogicalFiles')
    logical_file_count = int(logical_files_node.attrib['count'])
    with open(original_file_path, 'rb') as fobj:
        rp66v1file = File.FileRead(fobj)
        for logical_file_node in logical_files_node.iterfind('./LogicalFile'):
            logical_index.logical_files.append(read_logical_file_from_xml(logical_file_node, rp66v1file))
    if len(logical_index.logical_files) != logical_file_count:
        raise ExceptionRP66V1IndexXMLRead(
            f'Found {len(logical_index.logical_files)} logical Files but expected {logical_file_count}'
        )
    # Read the Visible Record section and construct a RLE for them. This is for IFLRs that only have their
    # LRSH position, EFLRs record their Visible Record position along with their LRSH position.
    rle_vr = xml_rle_read(xml_single_element(root, './VisibleRecords'))
    logical_index.visible_record_positions = LogicalFile.VisibleRecordPositions(rle_vr.values())
    return logical_index

class IndexResult(typing.NamedTuple):
    path_input: str
    size_input: int
    size_index: int
    time: float
    exception: bool
    ignored: bool


def index_a_single_file(path_in: str, path_out: str, private: bool) -> IndexResult:
    # logging.info(f'index_a_single_file(): "{path_in}" to "{path_out}"')
    bin_file_type = binary_file_type_from_path(path_in)
    if bin_file_type == 'RP66V1':
        if path_out:
            out_dir = os.path.dirname(path_out)
            if not os.path.exists(out_dir):
                logger.info(f'Making directory: {out_dir}')
                os.makedirs(out_dir, exist_ok=True)
        logger.info(f'Indexing {path_in} to {path_out}')
        try:
            with open(path_in, 'rb') as fobj:
                t_start = time.perf_counter()
                # index = RP66V1IndexXMLWrite(fobj, path_in)
                rp66v1_file = File.FileRead(fobj)
                logical_file_sequence = LogicalFile.LogicalIndex(rp66v1_file, path_in)
                if path_out:
                    with open(path_out + '.xml', 'w') as f_out:
                        write_logical_file_sequence_to_xml(logical_file_sequence, f_out, private)
                    index_size = os.path.getsize(path_out + '.xml')
                else:
                    xml_fobj = io.StringIO()
                    write_logical_file_sequence_to_xml(logical_file_sequence, xml_fobj, private)
                    index_output = xml_fobj.getvalue()
                    index_size = len(index_output)
                    print(index_output)
                result = IndexResult(
                    path_in,
                    os.path.getsize(path_in),
                    index_size,
                    time.perf_counter() - t_start,
                    False,
                    False,
                )
                logger.info(f'Length of XML: {index_size}')
                return result
        except ExceptionTotalDepthRP66V1:
            logger.exception(f'Failed to index with ExceptionTotalDepthRP66V1: {path_in}')
            return IndexResult(path_in, os.path.getsize(path_in), 0, 0.0, True, False)
        except Exception:
            logger.exception(f'Failed to index with Exception: {path_in}')
            return IndexResult(path_in, os.path.getsize(path_in), 0, 0.0, True, False)
    logger.info(f'Ignoring file type "{bin_file_type}" at {path_in}')
    return IndexResult(path_in, 0, 0, 0.0, False, True)


def index_dir_multiprocessing(dir_in: str, dir_out: str, private: bool, jobs: int) -> typing.Dict[str, IndexResult]:
    """Multiprocessing code to index in XML.
    Returns a dict of {path_in : IndexResult, ...}"""
    if jobs < 1:
        jobs = multiprocessing.cpu_count()
    logging.info('scan_dir_multiprocessing(): Setting multi-processing jobs to %d' % jobs)
    pool = multiprocessing.Pool(processes=jobs)
    tasks = [
        (t.filePathIn, t.filePathOut, private) for t in DirWalk.dirWalk(
            dir_in, dir_out, theFnMatch='', recursive=True, bigFirst=True
        )
    ]
    # print('tasks:')
    # pprint.pprint(tasks, width=200)
    # return {}
    results = [
        r.get() for r in [
            pool.apply_async(index_a_single_file, t) for t in tasks
        ]
    ]
    return {r.path_input : r for r in results}


def index_dir_or_file(path_in: str, path_out: str, recurse: bool, private: bool) -> typing.Dict[str, IndexResult]:
    logging.info(f'index_dir_or_file(): "{path_in}" to "{path_out}" recurse: {recurse}')
    ret = {}
    if os.path.isdir(path_in):
        for file_in_out in DirWalk.dirWalk(path_in, path_out, theFnMatch='', recursive=recurse, bigFirst=False):
            # print(file_in_out)
            ret[file_in_out.filePathIn] = index_a_single_file(file_in_out.filePathIn, file_in_out.filePathOut, private)
    else:
        ret[path_in] = index_a_single_file(path_in, path_out, private)
    return ret

GNUPLOT_PLT = """set logscale x
set grid
set title "XML Index of RP66V1 Files with IndexFile.py."
set xlabel "RP66V1 File Size (bytes)"
# set mxtics 5
# set xrange [0:3000]
# set xtics
# set format x ""

set logscale y
set ylabel "XML Index Rate (ms/Mb)"
# set yrange [1:1e5]
# set ytics 20
# set mytics 2
# set ytics 8,35,3

set logscale y2
set y2label "Ratio index size / original size"
# set y2range [1e-4:10]
set y2tics

set pointsize 1
set datafile separator whitespace#"	"
set datafile missing "NaN"

# set fit logfile

# Curve fit, rate
rate(x) = 10**(a + b * log10(x))
fit rate(x) "{name}.dat" using 1:($3*1000/($1/(1024*1024))) via a, b

rate2(x) = 10**(5.5 - 0.5 * log10(x))

# Curve fit, size ratio
size_ratio(x) = 10**(c + d * log10(x))
fit size_ratio(x) "{name}.dat" using 1:($2/$1) via c,d
# By hand
# size_ratio2(x) = 10**(3.5 - 0.65 * log10(x))

# Curve fit, compression ratio
compression_ratio(x) = 10**(e + f * log10(x))
fit compression_ratio(x) "{name}.dat" using 1:($2/$1) via e,f

set terminal svg size 1000,700 # choose the file format
set output "{name}.svg" # choose the output device

# set key off

#set key title "Window Length"
#  lw 2 pointsize 2

# Fields: size_input, size_index, time, exception, ignored, path

plot "{name}.dat" using 1:($3*1000/($1/(1024*1024))) axes x1y1 title "XML Index Rate (ms/Mb)" lt 1 w points,\
    rate(x) title sprintf("Fit: 10**(%+.3g %+.3g * log10(x))", a, b) lt 1 lw 2, \
    "{name}.dat" using 1:($2/$1) axes x1y2 title "XML Index size / Original Size" lt 2 w points, \
    compression_ratio(x) title sprintf("Fit: 10**(%+.3g %+.3g * log10(x))", e, f) axes x1y2 lt 2 lw 2

# Plot size ratio:
#    "{name}.dat" using 1:($2/$1) axes x1y2 title "Index size ratio" lt 3 w points, \
#     size_ratio(x) title sprintf("Fit: 10**(%+.3g %+.3g * log10(x))", c, d) axes x1y2 lt 3 lw 2

reset
"""


def plot_gnuplot(data: typing.Dict[str, IndexResult], gnuplot_dir: str) -> None:
    if len(data) < 2:
        raise ValueError(f'Can not plot data with only {len(data)} points.')
    # First row is header row, create it then comment out the first item.
    table = [
        list(IndexResult._fields) + ['Path']
    ]
    table[0][0] = f'# {table[0][0]}'
    for k in sorted(data.keys()):
        if data[k].size_input > 0 and not data[k].exception:
            table.append(list(data[k]) + [k])
    name = 'IndexFile'
    return_code = gnuplot.invoke_gnuplot(gnuplot_dir, name, table, GNUPLOT_PLT.format(name=name))
    if return_code:
        raise IOError(f'Can not plot gnuplot with return code {return_code}')
    return_code = gnuplot.write_test_file(gnuplot_dir, 'svg')
    if return_code:
        raise IOError(f'Can not plot gnuplot with return code {return_code}')


def main() -> int:
    description = """usage: %(prog)s [options] file
    Scans a RP66V1 file or directory and writes out the index(es) in XML."""
    print('Cmd: %s' % ' '.join(sys.argv))
    parser = cmn_cmd_opts.path_in_out(
        description, prog='TotalDepth.RP66V1.ScanHTML.main', version=__version__, epilog=__rights__
    )
    cmn_cmd_opts.add_log_level(parser, level=20)
    cmn_cmd_opts.add_multiprocessing(parser)
    parser.add_argument(
        '-e', '--encrypted', action='store_true',
        help='Output encrypted Logical Records as well. [default: %(default)s]',
    )
    process.add_process_logger_to_argument_parser(parser)
    gnuplot.add_gnuplot_to_argument_parser(parser)
    parser.add_argument(
        '-p', '--private', action='store_true',
        help='Also write out private EFLRs. [default: %(default)s]',
    )
    args = parser.parse_args()
    # print('args:', args)
    # return 0
    cmn_cmd_opts.set_log_level(args)
    # Your code here
    clk_start = time.perf_counter()
    if os.path.isdir(args.path_in) and cmn_cmd_opts.multiprocessing_requested(args):
        result: typing.Dict[str, IndexResult] = index_dir_multiprocessing(
            args.path_in,
            args.path_out,
            args.private,
            args.jobs,
        )
    else:
        if args.log_process > 0.0:
            with process.log_process(args.log_process):
                result: typing.Dict[str, IndexResult] = index_dir_or_file(
                    args.path_in,
                    args.path_out,
                    args.recurse,
                    args.private,
                )
        else:
            result: typing.Dict[str, IndexResult] = index_dir_or_file(
                args.path_in,
                args.path_out,
                args.recurse,
                args.private,
            )
    clk_exec = time.perf_counter() - clk_start
    size_index = size_input = 0
    files_processed = 0
    try:
        header = (
            f'{"Size In":>16}',
            f'{"Size Out":>16}',
            f'{"Time":>8}',
            f'{"Ratio %":>8}',
            f'{"ms/Mb":>8}',
            f'{"Fail?":5}',
            f'Path',
        )
        print(' '.join(header))
        print(' '.join('-' * len(v) for v in header))
        for path in sorted(result.keys()):
            idx_result = result[path]
            if idx_result.size_input > 0:
                ms_mb = idx_result.time * 1000 / (idx_result.size_input / 1024 ** 2)
                ratio = idx_result.size_index / idx_result.size_input
                print(
                    f'{idx_result.size_input:16,d} {idx_result.size_index:16,d}'
                    f' {idx_result.time:8.3f} {ratio:8.3%} {ms_mb:8.1f} {str(idx_result.exception):5}'
                    f' "{path}"'
                )
                size_input += result[path].size_input
                size_index += result[path].size_index
                files_processed += 1
        if args.gnuplot:
            try:
                plot_gnuplot(result, args.gnuplot)
            except Exception:
                logger.exception('gunplot failed')
    except Exception as err:
        logger.exception(str(err))
    print('Execution time = %8.3f (S)' % clk_exec)
    if size_input > 0:
        ms_mb = clk_exec * 1000 / (size_input/ 1024**2)
        ratio = size_index / size_input
    else:
        ms_mb = 0.0
        ratio = 0.0
    print(f'Out of  {len(result):,d} processed {files_processed:,d} files of total size {size_input:,d} input bytes')
    print(f'Wrote {size_index:,d} output bytes, ratio: {ratio:8.3%} at {ms_mb:.1f} ms/Mb')
    print('Bye, bye!')
    return 0


if __name__ == '__main__':
    sys.exit(main())
