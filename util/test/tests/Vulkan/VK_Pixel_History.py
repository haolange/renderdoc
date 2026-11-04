import renderdoc as rd
import rdtest

class VK_Pixel_History(rdtest.Pixel_History):
    demos_test_name = 'VK_Pixel_History'
    internal = False


class VK_Pixel_History_MultiSubpass(rdtest.TestCase):
    demos_test_name = 'VK_Pixel_History'

    def check_capture(self):
        # Keep this regression bounded: two draw histories and three real implicit resolves.
        sub = rd.Subresource()
        for batch in ['Multi subpass', 'Multi subpass MSAA']:
            marker = self.find_action('Batch: ' + batch)
            draw = self.find_action('Simple Test', marker.eventId).nextAction
            self.set_event(draw.eventId, True)
            target = self.controller.GetPipelineState().GetOutputTargets()[0].resource
            expected = list(self.pick_pixel(target, 170, 140, sub, rd.CompType.UNorm).floatValue)
            with self.pixel_history(target, 170, 140, sub, rd.CompType.UNorm) as history:
                mods = [m for m in history.modifs if m.eventId == draw.eventId]
                assert len(mods) > 0
                assert rdtest.value_compare(mods[-1].postMod.col.floatValue, expected, eps=1.0/255.0)
            # A second query at the same event verifies that artificial End/Resume preserved it.
            restored = self.pick_pixel(target, 170, 140, sub, rd.CompType.UNorm).floatValue
            assert rdtest.value_compare(restored, expected, eps=1.0/255.0)

        resolved = self.get_resource_by_name('Multi subpass MSAA resolveImg').resourceId
        boundaries = [u.eventId for u in self.controller.GetUsage(resolved)
                      if u.usage == rd.ResourceUsage.ResolveDst]
        assert len(boundaries) == 3
        self.set_event(boundaries[-1], True)
        with self.pixel_history(resolved, 170, 140, sub, rd.CompType.UNorm) as history:
            resolve_mods = {m.eventId: m for m in history.modifs if m.eventId in boundaries}
            assert len(resolve_mods) == len(boundaries)
            before = [32.0/255.0, 64.0/255.0, 128.0/255.0, 1.0]
            for event in boundaries:
                # This destination is written only by the three resolves. Its pre-value is the
                # seeded sentinel, then the preceding boundary's independent post-value. A partial
                # ordinary replay before Next closes the pass and may itself execute a resolve,
                # so it cannot be used as an independent pre-boundary observation.
                self.set_event(event, True)
                after = self.pick_pixel(resolved, 170, 140, sub, rd.CompType.UNorm).floatValue
                assert rdtest.value_compare(resolve_mods[event].preMod.col.floatValue, before, eps=1.0/255.0), f'boundary {event}: pre={list(resolve_mods[event].preMod.col.floatValue)}, expected={list(before)}, post={list(resolve_mods[event].postMod.col.floatValue)}, expectedPost={list(after)}'
                assert rdtest.value_compare(resolve_mods[event].postMod.col.floatValue, after, eps=1.0/255.0)
                before = list(after)
        self.set_event(boundaries[-1], True)
