from pathlib import Path
from copy import deepcopy
import tempfile
import unittest
import uuid
import zipfile

import mido

from app.yue2_app.asset_library import AssetLibrary
from app.yue2_app.library_cleanup import LibraryCleanup
from app.yue2_app.midi_document import (MidiStore, VersionConflict, blank_document,
    parse_midi, export_midi, generation_check, validate_document)


def note(tick=0, pitch=60, duration=480):
    return {'id': uuid.uuid4().hex, 'tick': tick, 'pitch': pitch, 'duration': duration, 'velocity': 99}


class MidiDocumentTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.library = AssetLibrary(self.root)
        self.store = MidiStore(self.library)

    def tearDown(self):
        self.temp.cleanup()

    def test_roundtrip_preserves_raw_timing_tempo_meter_controls_and_overlapping_pitch(self):
        original = blank_document('中文 MIDI')
        original['ppq'] = 960
        original['tempos'].append({'tick': 3840, 'tempo': 750000})
        original['meters'] = [{'tick': 0, 'numerator': 3, 'denominator': 4},
                              {'tick': 5760, 'numerator': 6, 'denominator': 8}]
        original['tracks'][0]['notes'] = [note(13,60,701),note(128,60,1251),note(5760,72,960)]
        original['tracks'][0]['events'] = [{'tick': 32, 'meta': False,
            'message': {'type': 'control_change','channel':0,'control':64,'value':127,'time':0}},
            {'tick': 3840,'meta':False,'message':{'type':'pitchwheel','channel':0,'pitch':123,'time':0}}]
        original['tracks'][2]['notes'] = [note(960,38,60)]
        path = export_midi(original, self.root/'source.mid')
        restored = parse_midi(path)
        self.assertEqual(restored['ppq'], 960)
        self.assertEqual(restored['tempos'], original['tempos'])
        self.assertEqual(restored['meters'], original['meters'])
        melody = next(t for t in restored['tracks'] if t['role']=='melody')
        self.assertEqual(sorted((n['tick'],n['pitch'],n['duration'],n['velocity']) for n in melody['notes']),
                         sorted((n['tick'],n['pitch'],n['duration'],n['velocity']) for n in original['tracks'][0]['notes']))
        self.assertIn(original['tracks'][0]['events'][0], melody['events'])
        self.assertEqual(next(t for t in restored['tracks'] if t['role']=='drums')['notes'][0]['pitch'],38)

    def test_type_zero_splits_channels_without_blind_role_mapping(self):
        midi=mido.MidiFile(type=0,ticks_per_beat=480)
        track=mido.MidiTrack([mido.Message('note_on',channel=0,note=60,velocity=90),
            mido.Message('note_on',channel=9,note=38,velocity=100),
            mido.Message('note_off',channel=9,note=38,time=120),
            mido.Message('note_off',channel=0,note=60,time=360)])
        midi.tracks.append(track)
        midi.save(self.root/'zero.mid')
        result=parse_midi(self.root/'zero.mid')
        self.assertEqual([t['role'] for t in result['tracks']],['other','drums'])
        self.assertFalse(result['mapping_confirmed'])
        self.assertEqual(result['tracks'][0]['notes'][0]['duration'],480)

    def test_nested_same_pitch_notes_and_meter_clocks_roundtrip(self):
        data=blank_document()
        data['tracks'][0]['notes']=[note(0,60,4000),note(120,60,120),note(240,60,1000),note(240,60,120)]
        data['meters'][0].update(clocks_per_click=36,notated_32nd_notes_per_beat=12)
        output=export_midi(data,self.root/'nested.mid')
        restored=parse_midi(output)
        expected=sorted((n['tick'],n['duration'],n['pitch']) for n in data['tracks'][0]['notes'])
        actual=sorted((n['tick'],n['duration'],n['pitch']) for t in restored['tracks'] for n in t['notes'])
        self.assertEqual(actual,expected)
        self.assertEqual(restored['meters'],data['meters'])

    def test_failed_atomic_export_preserves_existing_file(self):
        from app.yue2_app.midi_document import atomic_payload
        target=export_midi(blank_document(),self.root/'existing.mid')
        original=target.read_bytes()
        def incomplete(stream):
            stream.write(b'broken')
            raise OSError('disk write failed')
        with self.assertRaises(OSError):
            atomic_payload(target,incomplete)
        self.assertEqual(target.read_bytes(),original)
        self.assertEqual(list(self.root.glob('m-*.tmp')),[])

    def test_single_role_export_ignores_unrelated_nested_voice_limit(self):
        data = blank_document()
        data['tracks'][0]['notes'] = [note()]
        data['tracks'][1]['notes'] = [note(i, 64, 2000 - 2 * i) for i in range(128)]
        output = export_midi(data, self.root / 'melody-only.mid', 'melody')
        messages = [message for track in mido.MidiFile(output).tracks for message in track]
        self.assertEqual([message.note for message in messages
                          if message.type == 'note_on' and message.velocity], [60])
        with self.assertRaisesRegex(ValueError, '128'):
            export_midi(data, self.root / 'combined.mid')

    def test_odd_ppq_source_retained_and_generation_grid_is_exact(self):
        data=blank_document()
        data['ppq']=7
        data['tracks'][0]['notes']=[note(1,60,3)]
        normal=export_midi(data,self.root/'odd-original.mid')
        projected=export_midi(data,self.root/'odd-model.mid','melody',model=True)
        self.assertEqual(mido.MidiFile(normal).ticks_per_beat,7)
        self.assertEqual(mido.MidiFile(projected).ticks_per_beat,480)
        restored=parse_midi(projected)
        for track in restored['tracks']:
            for event in track['notes']:
                self.assertEqual(event['tick']%120,0)
                self.assertEqual(event['duration']%120,0)

    def test_stage_group_keeps_one_asset_and_immutable_previous_revision(self):
        data=blank_document()
        data['tracks'][0]['notes']=[note()]
        doc=self.store.create(data)
        first=self.store.publish(doc['id'],doc['version'],group_update=True)
        path,_=self.library.revision_file(first['id'],first['current_revision_id'])
        old_bytes=path.read_bytes()
        data['tracks'][1]['notes']=[note(0,64,1920)]
        doc=self.store.save(doc['id'],data,doc['version'],'')
        second=self.store.publish(doc['id'],doc['version'],group_update=True)
        self.assertEqual(first['id'],second['id'])
        self.assertNotEqual(first['current_revision_id'],second['current_revision_id'])
        self.assertEqual(self.library.count_assets(),1)
        self.assertEqual(path.read_bytes(),old_bytes)
        self.assertEqual(second['metadata']['note_counts']['chord'],1)

    def test_published_version_pins_original_source_after_current_source_changes(self):
        from app.yue2_app.library_cleanup import LibraryCleanup
        original=export_midi(blank_document(),self.root/'original.mid')
        asset=self.library.import_file(original,kind='midi',title='original')
        data=blank_document()
        data['settings']['audio_source']={'source_path':{'$asset':asset['id'],'revision_id':asset['current_revision_id']}}
        doc=self.store.create(data)
        self.store.publish(doc['id'],doc['version'])
        data['settings'].pop('audio_source')
        self.store.save(doc['id'],data,doc['version'],'')
        cleanup=LibraryCleanup(self.library)
        cleanup.move([asset['id']],'trashed')
        preview=cleanup.preview({'ids':[asset['id']],'detach_projects':True})
        self.assertEqual(preview['deletable'],[])
        self.assertIn('草稿仍在使用',preview['skipped'][0]['reason'])
        frozen=self.store.get(doc['id'],1)
        self.assertEqual(frozen['settings']['audio_source']['source_path']['$asset'],asset['id'])

    def test_document_pagination_archive_restore_and_cache_cleanup_preserve_outputs(self):
        docs=[self.store.create(blank_document(f'doc {index}')) for index in range(23)]
        self.assertEqual(len(self.store.page('',20)['documents']),20)
        self.assertEqual(len(self.store.page('',20,20)['documents']),3)
        doc=docs[-1]
        self.store.archive(doc['id'],'')
        self.assertEqual(self.store.page('')['total'],22)
        self.assertEqual(self.store.page('',archived=True)['documents'][0]['id'],doc['id'])
        self.assertIsNone(self.store.current(''))
        with self.assertRaises(VersionConflict):
            self.store.save(doc['id'],doc,doc['version'],'')
        restored=self.store.archive(doc['id'],'',archived=False)['document']
        self.assertEqual(self.store.current('')['id'],doc['id'])
        self.assertEqual(restored['tracks'],doc['tracks'])
        cached=self.store.home/'cache'/('a'*64)
        cached.mkdir(parents=True)
        (cached/'notes.json').write_text('[]')
        (cached/'chords.json').write_text('[]')
        retained=self.store.home/'s'/'keep.mid'
        retained.parent.mkdir();retained.write_bytes(b'retained generation input')
        self.assertEqual(self.store.cache_info()['files'],2)
        self.assertEqual(self.store.clear_cache()['removed'],2)
        self.assertTrue(retained.exists())
        self.assertEqual(self.store.get(doc['id'])['tracks'],doc['tracks'])

    def test_bad_type_smpte_and_unterminated_input_are_rejected(self):
        for kind,ppq,events in [(2,480,[]),(0,-25,[]),(0,480,[mido.Message('note_on',note=60,velocity=100)])]:
            midi=mido.MidiFile(type=kind,ticks_per_beat=ppq)
            midi.tracks.append(mido.MidiTrack(events))
            midi.save(self.root/'bad.mid')
            with self.assertRaises(ValueError):
                parse_midi(self.root/'bad.mid')

    def test_cas_scope_restart_and_independent_drafts(self):
        p=self.library.create_project('歌曲 A')
        global_doc=self.store.create(blank_document())
        scoped=self.store.create(blank_document('歌曲 A MIDI'),p['id'])
        edited=deepcopy(scoped)
        edited['tracks'][0]['notes']=[note()]
        saved=self.store.save(scoped['id'],edited,scoped['version'],p['id'])
        with self.assertRaises(VersionConflict):
            self.store.save(scoped['id'],edited,scoped['version'],p['id'])
        with self.assertRaises(ValueError):
            self.store.save(scoped['id'],edited,saved['version'],'')
        fresh=MidiStore(AssetLibrary(self.root))
        self.assertEqual(fresh.current('')['id'],global_doc['id'])
        self.assertEqual(fresh.current(p['id'])['tracks'][0]['notes'],edited['tracks'][0]['notes'])

    def test_generation_projection_is_explicit_snapshot_does_not_mutate_draft(self):
        data=blank_document()
        data['tracks'][0]['notes']=[note(13,60,701)]
        data['tracks'][1]['notes']=[note(0,60,1920),note(0,64,1920),note(0,67,1920)]
        doc=self.store.create(data)
        check=generation_check(doc)
        self.assertTrue(check['ready'])
        self.assertEqual(check['changed_notes'],1)
        with self.assertRaises(ValueError):
            self.store.snapshot(doc['id'],doc['version'])
        recipe=self.store.snapshot(doc['id'],doc['version'],acknowledge_projection=True)
        original=Path(recipe['source']['melody_midi']).read_bytes()
        edited=deepcopy(doc)
        edited['tracks'][0]['notes'][0]['pitch']=72
        self.store.save(doc['id'],edited,doc['version'],'')
        self.assertEqual(Path(recipe['source']['melody_midi']).read_bytes(),original)
        self.assertEqual(parse_midi(Path(recipe['source']['melody_midi']))['tracks'][0]['notes'][0]['tick'],0)

    def test_empty_and_failure_exports_are_distinct_and_downloads_do_not_spam_assets(self):
        data=blank_document()
        data['extraction']={'tracks':{'melody':{'status':'empty'},'chord':{'status':'failed','error':'boom'},'drums':{'status':'empty'}}}
        doc=self.store.create(data)
        self.assertTrue(self.store.download(doc['id'],1,'melody').is_file())
        with self.assertRaises(ValueError):
            self.store.download(doc['id'],1,'chord')
        with zipfile.ZipFile(self.store.download(doc['id'],1,'zip')) as archive:
            self.assertEqual(set(archive.namelist()),{'melody.mid','drums.mid','manifest.json'})
        self.assertEqual(self.library.count_assets(),0)
        first=self.store.publish(doc['id'],1)
        second=self.store.publish(doc['id'],1)
        self.assertEqual(first['id'],second['id'])

    def test_long_documents_export_without_generation_grid_limit(self):
        data=blank_document()
        data['tracks'][0]['notes']=[note(600000,60,480)]
        self.assertFalse(generation_check(data,True)['ready'])
        self.assertTrue(export_midi(data,self.root/'long.mid').is_file())

    def test_pitch_bounds_and_duplicate_ids_cannot_be_silently_clamped(self):
        data=blank_document()
        data['tracks'][0]['notes']=[note(pitch=128)]
        with self.assertRaises(ValueError):
            validate_document(data)
        data['tracks'][0]['notes']=[note(),note()]
        data['tracks'][0]['notes'][1]['id']=data['tracks'][0]['notes'][0]['id']
        with self.assertRaises(ValueError):
            validate_document(data)

    def test_source_asset_is_protected_from_cleanup(self):
        export_midi(blank_document(),self.root/'source.mid')
        asset=self.library.import_file(self.root/'source.mid',kind='midi')
        data=blank_document()
        data['source']={'asset_id':asset['id'],'revision_id':asset['current_revision_id']}
        self.store.create(data)
        cleanup=LibraryCleanup(self.library)
        cleanup.move([asset['id']],'trashed')
        self.assertFalse(cleanup.preview({'ids':[asset['id']],'detach_projects':True})['deletable'])


if __name__=='__main__':
    unittest.main()
